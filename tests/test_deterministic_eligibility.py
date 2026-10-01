from datetime import datetime
from typing import Optional, List, Dict, Any
import pytest

from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
    DeterministicDecision,
    DeterministicEligibility,
    FitEligibility,
    ExperienceLevel,
    ConfidenceLevel,
)
from src.eligibility import DeterministicJobEvaluator
from src.scorer import JobRelevanceScorer


def make_job(
    platform: str = "freehire",
    platform_job_id: str = "job-1",
    title: str = "Java Developer",
    company: str = "Acme Corp",
    location: Optional[str] = "Bengaluru",
    url: str = "https://example.com/job/1",
    description_snippet: Optional[str] = None,
    source_experience_years_min: Optional[int] = None,
    structured_requirements: Optional[List[Dict[str, Any]]] = None,
    work_mode: Optional[str] = None,
) -> JobRecord:
    return JobRecord(
        platform=platform,
        platform_job_id=platform_job_id,
        title=title,
        company=company,
        location=location,
        url=url,
        normalized_url=url,
        description_snippet=description_snippet,
        source_experience_years_min=source_experience_years_min,
        structured_requirements=structured_requirements,
        work_mode=work_mode,
        validation=ValidationEvidence(
            job_status=JobStatus.ACTIVE,
            application_type=ApplicationType.COMPANY_DIRECT,
            evidence_source="unit_test",
            evidence_notes="mocked active listing",
            application_url=url,
        ),
    )


class TestDeterministicEligibility:
    """
    Phase 3B Test Suite: Deterministic job eligibility & matching preparation.
    Verifies that eligibility evaluation is explainable, strictly adheres to known
    candidate constraints, never invents requirements, and cannot be overridden by
    semantic scores.
    """

    @pytest.fixture
    def standard_candidate(self) -> CandidateProfile:
        return CandidateProfile(
            name="Abhishek Dwivedi",
            total_experience_years=2.0,
            experience_confirmed=True,
            hard_limit_years=4.0,
            experience_range_min=1.0,
            experience_range_max=3.0,
            target_title="Java Backend Developer",
            target_locations=["Bengaluru", "Hyderabad", "Pune"],
            remote_preference="flexible",
            preferences_source="user_configured",
        )

    @pytest.fixture
    def evaluator(self) -> DeterministicJobEvaluator:
        return DeterministicJobEvaluator()

    def test_job_within_known_candidate_constraints_is_eligible(self, standard_candidate, evaluator):
        """A verified Java job requiring 2 YOE in Bengaluru should be deterministically ELIGIBLE."""
        job = make_job(
            platform="freehire",
            platform_job_id="fh-101",
            title="Junior Java Backend Developer",
            company="Infosys",
            location="Bengaluru, Karnataka, India",
            description_snippet="Looking for Java Backend Developer with 2 years experience in Spring Boot and REST APIs.",
            source_experience_years_min=2,
            structured_requirements=[{"text": "2 years Java experience", "priority": "required"}],
        )

        result = evaluator.evaluate(job, standard_candidate)
        assert result.decision == DeterministicDecision.ELIGIBLE
        assert result.is_eligible is True
        assert len(result.rejection_reasons) == 0
        assert result.criteria_breakdown.get("experience") == "ELIGIBLE"
        assert result.criteria_breakdown.get("location_and_remote") == "ELIGIBLE"

    def test_job_explicit_experience_conflicts_with_confirmed_candidate_hard_limit_rejected(
        self, standard_candidate, evaluator
    ):
        """A job requiring 5+ or 7+ years exceeds candidate hard limit (4.0 YOE) and must be REJECTED."""
        job = make_job(
            platform="linkedin",
            platform_job_id="li-202",
            title="Lead Java Architect",
            company="Oracle",
            location="Bengaluru",
            description_snippet="Must have minimum 7+ years of professional backend architecture experience.",
        )

        result = evaluator.evaluate(job, standard_candidate)
        assert result.decision == DeterministicDecision.REJECTED
        assert result.is_eligible is False
        assert any("exceeding candidate hard cutoff" in r for r in result.rejection_reasons)

    def test_unknown_candidate_experience_returns_needs_review_not_rejected(self, evaluator):
        """When candidate experience is unknown or unconfirmed, never reject; return NEEDS_REVIEW."""
        unconfirmed_candidate = CandidateProfile(
            name="New User",
            total_experience_years=None,
            experience_confirmed=False,
            hard_limit_years=4.0,
            target_cities=["Bengaluru"],
            remote_preference="flexible",
        )

        job = make_job(
            platform="indeed",
            platform_job_id="ind-303",
            title="Java Software Engineer",
            company="Wipro",
            location="Bengaluru",
            description_snippet="Requirements: 2 years of Java development experience.",
        )

        result = evaluator.evaluate(job, unconfirmed_candidate)
        assert result.decision == DeterministicDecision.NEEDS_REVIEW
        assert result.is_eligible is True  # provisional pending review
        assert any("unconfirmed or unknown" in r for r in result.review_reasons)

    def test_missing_job_experience_requirement_returns_needs_review_never_treated_as_zero(
        self, standard_candidate, evaluator
    ):
        """Missing job experience must NEVER be treated as 0 YOE or rejected; it requires human review."""
        job = make_job(
            platform="freehire",
            platform_job_id="fh-404",
            title="Java Developer",
            company="Tech Corp",
            location="Bengaluru",
            description_snippet="Build scalable web applications using Java and Spring Boot.",
            source_experience_years_min=None,
        )

        result = evaluator.evaluate(job, standard_candidate)
        assert result.decision == DeterministicDecision.NEEDS_REVIEW
        assert result.is_eligible is True
        assert any("unspecified or unretrieved" in r for r in result.review_reasons)

    def test_location_and_remote_preferences(self, evaluator):
        """Location and remote work format constraints must be enforced deterministically."""
        # Case A: Candidate is remote-only, job is onsite in Pune
        remote_only_candidate = CandidateProfile(
            name="Remote Candidate",
            total_experience_years=2.0,
            experience_confirmed=True,
            hard_limit_years=4.0,
            target_cities=["Pune"],
            remote_preference="remote",
        )
        onsite_job = make_job(
            platform="indeed",
            platform_job_id="ind-501",
            title="Java Engineer (Onsite)",
            company="Barclays",
            location="Pune, India",
            description_snippet="This is a strictly onsite office-based position. 2-3 years Java required.",
        )
        res_a = evaluator.evaluate(onsite_job, remote_only_candidate)
        assert res_a.decision == DeterministicDecision.REJECTED
        assert any("remote work" in r for r in res_a.rejection_reasons)

        # Case B: Candidate is remote-only, job is remote
        remote_job = make_job(
            platform="indeed",
            platform_job_id="ind-502",
            title="Remote Java Engineer",
            company="Remote First Inc",
            location="Remote - India",
            description_snippet="100% Work from home. 2-3 years experience required.",
        )
        res_b = evaluator.evaluate(remote_job, remote_only_candidate)
        assert res_b.decision == DeterministicDecision.ELIGIBLE

        # Case C: Candidate targets Indian tech hubs, job is abroad in Tokyo
        standard_candidate = CandidateProfile(
            name="Abhishek",
            total_experience_years=2.0,
            experience_confirmed=True,
            hard_limit_years=4.0,
            target_cities=["Bengaluru", "Pune", "Hyderabad"],
            remote_preference="flexible",
        )
        foreign_job = make_job(
            platform="linkedin",
            platform_job_id="li-503",
            title="Java Developer",
            company="Rakuten",
            location="Tokyo, Japan",
            description_snippet="Visa sponsorship available. 2 years experience.",
        )
        res_c = evaluator.evaluate(foreign_job, standard_candidate)
        assert res_c.decision == DeterministicDecision.REJECTED
        assert any("outside candidate target geography" in r for r in res_c.rejection_reasons)

    def test_ambiguous_requirements_return_needs_review_rather_than_unsupported_rejection(
        self, standard_candidate, evaluator
    ):
        """
        Contradictory or ambiguous experience indicators (e.g. Senior title with no numbers)
        must return NEEDS_REVIEW, not REJECTED.
        """
        ambiguous_job = make_job(
            platform="freehire",
            platform_job_id="fh-601",
            title="Senior Java Developer",
            company="Startup",
            location="Bengaluru",
            description_snippet="Join our backend core team. Java and Spring Boot experience required.",
            # No explicit numbers
        )
        result = evaluator.evaluate(ambiguous_job, standard_candidate)
        assert result.decision == DeterministicDecision.NEEDS_REVIEW
        assert result.is_eligible is True
        assert any("ambiguous" in r for r in result.review_reasons)

    def test_ai_or_semantic_scores_never_override_hard_rejection(self, standard_candidate, evaluator):
        """
        Even with a 100% skill match (Java, Spring Boot, Microservices, Hibernate, Kafka, Docker),
        a deterministically REJECTED job MUST produce FitEligibility.LOW_FIT and cannot be promoted.
        """
        job = make_job(
            platform="linkedin",
            platform_job_id="li-701",
            title="Staff Java Backend Engineer",
            company="BigTech",
            location="Bengaluru",
            description_snippet="Java 17, Spring Boot, Microservices, REST APIs, Hibernate, SQL, Kafka, Docker. Minimum 8+ years experience required.",
        )

        det_eligibility = evaluator.evaluate(job, standard_candidate)
        assert det_eligibility.decision == DeterministicDecision.REJECTED

        scorer = JobRelevanceScorer(target_location="Bengaluru", candidate_profile=standard_candidate)
        relevance = scorer.score_job(
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description_snippet,
            deterministic_eligibility=det_eligibility,
        )

        # Invariant: Must be LOW_FIT regardless of skill points earned
        assert relevance.eligibility == FitEligibility.LOW_FIT
        assert "Deterministic rejection" in relevance.eligibility_reason
        assert relevance.total_score > 0  # Points may be tallied transparently, but fit eligibility is locked

    def test_structured_freehire_fields_and_multiple_sources(self, standard_candidate, evaluator):
        """
        Structured source_experience_years_min from Freehire must be evaluated reliably
        even when the description snippet does not contain textual numbers.
        """
        # Freehire with 6 YOE structured min -> rejected against 4.0 limit
        fh_senior = make_job(
            platform="freehire",
            platform_job_id="fh-801",
            title="Java Backend Engineer",
            company="Enterprise Corp",
            location="Bengaluru",
            description_snippet="Join our backend systems team working with Java and Spring.",
            source_experience_years_min=6,
        )
        res_senior = evaluator.evaluate(fh_senior, standard_candidate)
        assert res_senior.decision == DeterministicDecision.REJECTED
        assert any("Structured source requirement" in r for r in res_senior.rejection_reasons)

        # Freehire with 2 YOE structured min -> eligible
        fh_junior = make_job(
            platform="freehire",
            platform_job_id="fh-802",
            title="Java Backend Engineer",
            company="Enterprise Corp",
            location="Bengaluru",
            description_snippet="Join our backend systems team working with Java and Spring.",
            source_experience_years_min=2,
        )
        res_junior = evaluator.evaluate(fh_junior, standard_candidate)
        assert res_junior.decision == DeterministicDecision.ELIGIBLE

    def test_stretch_range_spanning_hard_limit_is_needs_review_not_hard_rejection(
        self, standard_candidate, evaluator
    ):
        """
        When a requirement spans candidate limit (e.g., '2 to 5 years'),
        it should be treated as a potential stretch / NEEDS_REVIEW, not a hard rejection.
        """
        job = make_job(
            platform="indeed",
            platform_job_id="ind-901",
            title="Java Backend Developer",
            company="Fintech",
            location="Bengaluru",
            description_snippet="Looking for 2 to 5 years of experience in Java and Spring Boot.",
        )
        result = evaluator.evaluate(job, standard_candidate)
        assert result.decision == DeterministicDecision.NEEDS_REVIEW
        assert result.is_eligible is True
        assert any("stretch" in r or "spans candidate limit" in r for r in result.review_reasons)

    def test_unrelated_negative_domain_rejected(self, standard_candidate, evaluator):
        """Roles in completely unrelated domains (e.g., mobile Flutter/iOS) without Java backend match should be rejected."""
        job = make_job(
            platform="indeed",
            platform_job_id="ind-902",
            title="iOS Swift Developer",
            company="App Studio",
            location="Bengaluru",
            description_snippet="Build native iOS apps in Swift and Objective-C.",
        )
        result = evaluator.evaluate(job, standard_candidate)
        assert result.decision == DeterministicDecision.REJECTED
        assert any("role/domain" in r for r in result.rejection_reasons)
