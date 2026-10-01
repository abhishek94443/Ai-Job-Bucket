import pytest
from src.scorer import JobRelevanceScorer, ScoringWeights
from src.experience_classifier import ExperienceClassifier
from src.models import ExperienceLevel


def test_strong_java_spring_match():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title = "Java Backend Developer"
    company = "FinTech Labs"
    location = "Bengaluru, Karnataka, India"
    description = """
    We are looking for a Java Developer with 2-3 years of experience.
    Must have hands-on experience in Java 17, Spring Boot, Microservices,
    REST APIs, Hibernate/JPA, and PostgreSQL. Docker and Kafka are plus.
    """
    score = scorer.score_job(title, company, location, description)

    # Experience should be JUNIOR (2-3 years)
    assert score.experience_level == ExperienceLevel.JUNIOR
    # High relevance expected (>= 80)
    assert score.total_score >= 80
    assert "java" in score.matched_skills
    assert "spring boot" in score.matched_skills
    assert "microservices" in score.matched_skills
    assert score.score_breakdown["title_match"] == 25
    assert score.score_breakdown["location_fit"] == 15
    assert score.score_breakdown["experience_fit"] == 20


def test_irrelevant_frontend_role():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title = "Senior Frontend React Developer"
    company = "Design Co"
    location = "Bengaluru"
    description = "Expert in React, TypeScript, CSS, HTML5, Redux with 5+ years experience."

    score = scorer.score_job(title, company, location, description)
    # Title has negative role keywords, lacks Java backend
    assert score.total_score < 40
    assert "java" not in score.matched_skills
    assert score.score_breakdown["title_match"] <= 10


def test_senior_role_penalized_for_two_yoe_candidate():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title = "Senior Principal Java Architect"
    company = "MegaCorp"
    location = "Bengaluru"
    description = """
    Requires minimum 10+ years of experience leading engineering teams.
    Deep mastery of Java, Spring Boot, Kubernetes, and distributed architecture.
    """
    score = scorer.score_job(title, company, location, description)

    # Classifies as SENIOR
    assert score.experience_level == ExperienceLevel.SENIOR
    # Experience fit is penalized to 2 pts
    assert score.score_breakdown["experience_fit"] == 2
    assert any("Senior requirement (5+ yrs/Senior title) exceeds target experience" in r for r in score.reasons)


def test_missing_description_safe_provisional_scoring():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title = "Backend Developer (Java)"
    company = "Cloud Corp"
    location = "Bengaluru"
    description = None

    score = scorer.score_job(title, company, location, description)
    # Handles None gracefully
    assert score.experience_level == ExperienceLevel.UNKNOWN
    assert score.total_score > 0
    assert any("Description missing/unretrievable" in r for r in score.reasons)


def test_contradictory_experience_requirements():
    title = "Junior Software Engineer"
    description = "Must have 8-10 years of experience in enterprise systems, or 1-2 years in junior capacity."
    exp_level, reason = ExperienceClassifier.classify(title, description)
    assert exp_level == ExperienceLevel.UNKNOWN
    assert "Contradictory" in reason


def test_location_mismatch():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title = "Java Software Engineer"
    company = "EuroTech"
    location = "Berlin, Germany"
    description = "Java 17, Spring Boot, REST APIs, 2 years experience."

    score = scorer.score_job(title, company, location, description)
    # Location fit should be 0
    assert score.score_breakdown["location_fit"] == 0
    assert any("Location mismatch" in r for r in score.reasons)


def test_stable_deterministic_scores():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title = "Java Backend Developer"
    company = "SaaS Co"
    location = "Bengaluru"
    description = "Java, Spring Boot, SQL, REST APIs, 2 years of experience."

    score1 = scorer.score_job(title, company, location, description)
    score2 = scorer.score_job(title, company, location, description)

    assert score1.total_score == score2.total_score
    assert score1.score_breakdown == score2.score_breakdown
    assert score1.matched_skills == score2.matched_skills
    assert score1.reasons == score2.reasons


def test_strong_description_scores_better_than_title_only():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    
    # Title-only listing (e.g. from search card)
    title_only_score = scorer.score_job(
        title="Backend Developer (Java)",
        company="Company A",
        location="Bengaluru, Karnataka, India",
        description=None
    )
    
    # Full listing with verified skills
    full_listing_score = scorer.score_job(
        title="Backend Developer (Java)",
        company="Company A",
        location="Bengaluru, Karnataka, India",
        description="Developing scalable backend services using Java 17, Spring Boot, Microservices, Hibernate, and PostgreSQL with 2 years of experience."
    )

    assert full_listing_score.total_score > title_only_score.total_score
    assert full_listing_score.confidence_level.value == "HIGH"
    assert title_only_score.confidence_level.value == "LOW"
    assert title_only_score.is_provisional is True
    assert full_listing_score.is_provisional is False


def test_four_plus_years_explicit_requirement_penalized():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title = "Backend Developer (Java) - 4+ Years"
    company = "SAP"
    location = "Bengaluru, Karnataka, India"
    description = "4+ years of relevant experience in Java enterprise application development."

    score = scorer.score_job(title, company, location, description)
    # Experience requirement of 4+ years is recognized (MID_LEVEL stretch)
    assert score.experience_level == ExperienceLevel.MID_LEVEL
    # Experience fit is heavily penalized compared to optimal 1-3 years (6 vs 20)
    assert score.score_breakdown["experience_fit"] == 6
    assert any("stretch for ~2 YOE candidate" in r for r in score.reasons)


def test_junior_ranks_above_four_plus_years_all_else_equal():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    # Both have same title match, location match, and single Java skill match
    junior_job = scorer.score_job(
        title="Backend Developer (Java)",
        company="Company A",
        location="Bengaluru",
        description="We need a Java developer with 1-3 years of experience in backend services."
    )
    four_plus_job = scorer.score_job(
        title="Backend Developer (Java)",
        company="Company B",
        location="Bengaluru",
        description="We need a Java developer with 4+ years of experience in backend services."
    )
    assert junior_job.total_score > four_plus_job.total_score
    assert junior_job.score_breakdown["experience_fit"] == 20
    assert four_plus_job.score_breakdown["experience_fit"] == 6


def test_five_to_seven_years_stronger_penalty():
    scorer = JobRelevanceScorer(target_location="bengaluru")
    score_5_7 = scorer.score_job(
        title="Backend Developer (Java)",
        company="Company C",
        location="Bengaluru",
        description="Candidate must possess 5-7 years of extensive experience in Java enterprise development."
    )
    assert score_5_7.experience_level == ExperienceLevel.SENIOR
    # 5-7 years receives stronger mismatch penalty (2 pts vs 6 pts for 4+ yrs vs 20 pts for junior)
    assert score_5_7.score_breakdown["experience_fit"] == 2


def test_title_and_location_alone_cannot_qualify_high_fit():
    from src.models import FitEligibility
    scorer = JobRelevanceScorer(target_location="bengaluru")
    title_loc_only = scorer.score_job(
        title="Java Backend Developer",
        company="Big Tech",
        location="Bengaluru, Karnataka, India",
        description=None
    )
    # Even if raw score is >= 50 or 60, it MUST be PROVISIONAL_NEEDS_REVIEW because description is missing
    assert title_loc_only.eligibility == FitEligibility.PROVISIONAL_NEEDS_REVIEW
    assert title_loc_only.is_provisional is True


def test_one_skill_alone_cannot_qualify_high_fit():
    from src.models import FitEligibility
    scorer = JobRelevanceScorer(target_location="bengaluru")
    # Has description but only confirms 'java', no backend frameworks (Spring Boot, REST, SQL, etc.)
    one_skill_job = scorer.score_job(
        title="Backend Java Developer",
        company="Nuchange",
        location="Bengaluru",
        description="Responsible for writing core Java routines with 2 years of experience."
    )
    # Lacks multi-skill framework coverage, so cannot be HIGH_FIT
    assert one_skill_job.eligibility != FitEligibility.HIGH_FIT


def test_rich_java_spring_description_qualifies_high_fit():
    from src.models import FitEligibility
    scorer = JobRelevanceScorer(target_location="bengaluru")
    rich_job = scorer.score_job(
        title="Java Backend Developer",
        company="FinTech Corp",
        location="Bengaluru",
        description="Looking for Java 17, Spring Boot, Microservices, REST APIs, and PostgreSQL with 2-3 years of experience."
    )
    assert rich_job.eligibility == FitEligibility.HIGH_FIT
    assert rich_job.confidence_level.value == "HIGH"
    assert rich_job.total_score >= 80


def test_high_relevance_does_not_alter_listing_status():
    from src.models import JobRecord, ValidationEvidence, JobStatus, ApplicationType
    job = JobRecord(
        platform="indeed",
        platform_job_id="test101",
        title="Java Springboot Developer",
        company="Global Systems",
        url="https://in.indeed.com/viewjob?jk=test101",
        normalized_url="https://in.indeed.com/viewjob?jk=test101",
        validation=ValidationEvidence(
            job_status=JobStatus.UNKNOWN,
            application_type=ApplicationType.EXTERNAL,
            evidence_source="search_api",
            evidence_notes="Provisional search record"
        )
    )
    scorer = JobRelevanceScorer()
    score = scorer.score_job(
        job.title,
        job.company,
        "Bengaluru",
        "We are looking for a Java Backend Engineer with 2-3 years of experience in Java 17, Spring Boot, Microservices, REST APIs, and PostgreSQL database."
    )
    job.relevance = score
    # High score must NEVER mutate conservative UNKNOWN status
    assert job.validation.job_status == JobStatus.UNKNOWN
    assert job.relevance.total_score >= 70


def test_five_to_seven_years_classified_as_senior():
    title = "Backend Developer"
    description = "Candidate must possess 5-7 years of extensive experience in software development."
    exp_level, reason = ExperienceClassifier.classify(title, description)
    assert exp_level == ExperienceLevel.SENIOR
    assert "5.0+" in reason or "5" in reason


def test_senior_title_without_explicit_years():
    title = "Lead Java Architect"
    description = "Responsible for architectural roadmap and team technical guidance."
    exp_level, reason = ExperienceClassifier.classify(title, description)
    assert exp_level == ExperienceLevel.SENIOR
    assert "Title indicates" in reason


def test_contradictory_title_and_description():
    title = "Junior Java Developer"
    description = "We are seeking a candidate with 6+ years of core engineering experience."
    exp_level, reason = ExperienceClassifier.classify(title, description)
    assert exp_level == ExperienceLevel.UNKNOWN
    assert "Contradiction" in reason
