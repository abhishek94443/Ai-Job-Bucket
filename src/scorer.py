import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from .models import (
    RelevanceScore,
    ExperienceLevel,
    ExperienceEligibility,
    ConfidenceLevel,
    FitEligibility,
    DeterministicDecision,
    DeterministicEligibility,
)
from .experience_classifier import ExperienceClassifier
from .candidate_profile import DEFAULT_CANDIDATE_PROFILE, CandidateProfile


class ScoringWeights(BaseModel):
    title_match_weight: int = 25
    skills_match_weight: int = 40
    experience_fit_weight: int = 20
    location_fit_weight: int = 15


class JobRelevanceScorer:
    """
    Transparent, explainable relevance scorer for Java Backend Developer (approx 2 YOE).
    Weights sum to 100:
    - Title Match: up to 25
    - Skills Match: up to 40
    - Experience Fit: up to 20
    - Location Fit: up to 15
    """

    CORE_SKILLS = [
        ("java", r"\bjava(?:\s+17|\s+21)?\b"),
        ("spring boot", r"\bspring\s*boot\b"),
        ("spring / spring mvc", r"\bspring(?:\s+mvc|\s+batch|\s+cloud)?\b"),
        ("rest api", r"\b(?:rest|restful)\s*api[s]?\b|\brest\b"),
        ("microservices", r"\bmicroservices?\b"),
        ("hibernate / jpa", r"\b(?:hibernate|jpa)\b"),
        ("spring security", r"\bspring\s*security\b|\boauth2?\b|\bjwt\b"),
        ("sql / postgresql / mysql", r"\b(?:sql|postgresql|postgres|mysql|oracle(?:\s+sql)?)\b"),
        ("kafka / jms", r"\b(?:kafka|jms|activemq|redis|message\s*queue)\b"),
        ("docker", r"\bdocker\b|\bkubernetes\b"),
        ("maven", r"\bmaven\b"),
        ("junit", r"\bjunit\b|\bunit\s*test"),
        ("ci/cd", r"\b(?:ci[\/-]?cd|jenkins)\b"),
    ]

    NEGATIVE_ROLE_KEYWORDS = [
        "frontend", "front-end", "react", "angular", "vue", "ios", "android",
        "flutter", "devops engineer", "qa engineer", "data engineer", "data scientist",
        ".net", "c#", "php", "ruby on rails"
    ]

    def __init__(
        self,
        weights: Optional[ScoringWeights] = None,
        target_location: str = "bengaluru",
        candidate_profile: Optional[CandidateProfile] = None
    ):
        self.weights = weights or ScoringWeights()
        self.target_location = target_location.lower()
        self.profile = candidate_profile or DEFAULT_CANDIDATE_PROFILE

    def score_job(
        self,
        title: str,
        company: str,
        location: Optional[str] = None,
        description: Optional[str] = None,
        deterministic_eligibility: Optional[DeterministicEligibility] = None,
        source_experience_years_min: Optional[int] = None,
    ) -> RelevanceScore:
        t_lower = (title or "").lower()
        d_lower = (description or "").lower()
        loc_lower = (location or "").lower()
        combined_text = f"{t_lower} {d_lower}"

        breakdown: Dict[str, int] = {}
        reasons: List[str] = []
        matched_skills: List[str] = []
        missing_critical_skills: List[str] = []

        # 1. Title Match (up to title_match_weight)
        title_score = 0
        is_java_title = "java" in t_lower
        is_backend_title = any(w in t_lower for w in ["backend", "back-end", "back end", "software engineer", "developer", "sde"])
        
        # Check negative roles in title
        has_negative_title = any(re.search(rf"\b{re.escape(k)}\b", t_lower) for k in self.NEGATIVE_ROLE_KEYWORDS)

        if is_java_title and is_backend_title:
            title_score = self.weights.title_match_weight
            reasons.append(f"Title directly matches Java Backend/Software Developer (+{title_score})")
        elif is_java_title:
            title_score = int(self.weights.title_match_weight * 0.8)
            reasons.append(f"Title matches Java role (+{title_score})")
        elif is_backend_title:
            title_score = int(self.weights.title_match_weight * 0.4)
            reasons.append(f"Title matches generic Backend/Developer role (+{title_score})")
        else:
            title_score = 0
            reasons.append("Title does not clearly indicate Java/Backend role (+0)")

        if has_negative_title:
            title_score = max(0, title_score - 15)
            reasons.append("Title includes secondary/irrelevant domain indicator (-15)")

        breakdown["title_match"] = title_score

        # 2. Skills Match (up to skills_match_weight)
        # Check actual evidence in description (or title)
        for skill_name, pattern in self.CORE_SKILLS:
            if re.search(pattern, combined_text):
                matched_skills.append(skill_name)

        # Critical primary skills: Java, Spring Boot / Spring, REST / Microservices, SQL
        critical_skills = ["java", "spring boot", "rest api", "sql / postgresql / mysql"]
        for crit in critical_skills:
            if crit not in matched_skills:
                missing_critical_skills.append(crit)

        if not description or not description.strip():
            # Missing description -> conservative skill scoring
            # Give base points if title has java, otherwise 0
            skills_score = 10 if is_java_title else 0
            reasons.append(f"Description missing/unretrievable; provisional skill score based on title evidence (+{skills_score})")
        else:
            # Score proportionally based on matched skills out of CORE_SKILLS
            ratio = len(matched_skills) / len(self.CORE_SKILLS)
            skills_score = round(ratio * self.weights.skills_match_weight)
            # Bonus for core stack combination (Java + Spring Boot)
            if "java" in matched_skills and "spring boot" in matched_skills:
                skills_score = min(self.weights.skills_match_weight, skills_score + 5)
            reasons.append(f"Matched {len(matched_skills)} core skills from description (+{skills_score})")

        breakdown["skills_match"] = skills_score

        # 3. Experience Fit (up to experience_fit_weight = 20)
        # Candidate has ~2 years experience
        exp_level, exp_reason = ExperienceClassifier.classify(title, description)
        exp_eligibility, elig_notes = ExperienceClassifier.evaluate_eligibility(
            exp_level, exp_reason, title, description
        )

        # Incorporate source-provided structured experience if present
        if source_experience_years_min is not None:
            if source_experience_years_min > (self.profile.hard_limit_years or 4.0):
                exp_eligibility = ExperienceEligibility.OVER_LIMIT
                exp_level = ExperienceLevel.SENIOR
                exp_reason = f"Source-provided minimum experience ({source_experience_years_min}+ yrs) exceeds hard limit of {self.profile.hard_limit_years or 4.0:g} years"

        exp_score = 0

        # Detect specific numerical ranges from reason
        is_4_plus = "4+" in exp_reason or "4.0+" in exp_reason or "4-" in exp_reason or "4 to " in exp_reason
        is_5_plus = "5" in exp_reason or "6" in exp_reason or "7" in exp_reason or "8" in exp_reason or "10" in exp_reason

        if exp_eligibility == ExperienceEligibility.MATCH:
            if exp_level == ExperienceLevel.ENTRY_LEVEL:
                exp_score = int(self.weights.experience_fit_weight * 0.8)  # 16 pts
                reasons.append(f"Entry-level role fit: {exp_reason} (+{exp_score})")
            else:
                exp_score = self.weights.experience_fit_weight  # optimal fit (1-3 yrs): 20 pts
                reasons.append(f"Optimal experience fit for ~2 YOE candidate: {exp_reason} (+{exp_score})")
        elif exp_eligibility == ExperienceEligibility.STRETCH:
            if is_4_plus:
                exp_score = 6  # 6 pts
                reasons.append(f"Explicit 4+ years requirement is a significant stretch for ~2 YOE candidate: {exp_reason} (+{exp_score})")
            else:
                exp_score = 10  # 10 pts
                reasons.append(f"Mid-level role; moderate stretch fit for 2 YOE: {exp_reason} (+{exp_score})")
        elif exp_eligibility == ExperienceEligibility.OVER_LIMIT or is_5_plus or exp_level == ExperienceLevel.SENIOR:
            exp_score = 2  # 2 pts
            reasons.append(f"Senior requirement (5+ yrs/Senior title) exceeds target experience and hard limit of 4 years: {exp_reason} (+{exp_score})")
        else:  # UNKNOWN
            exp_score = 8  # conservative provisional fit (8 pts)
            reasons.append(f"Experience requirements uncertain: {exp_reason} (+{exp_score})")

        breakdown["experience_fit"] = exp_score

        # 4. Location Fit (up to location_fit_weight = 15)
        # Target prioritized cities in India: Bengaluru, Delhi, Gurgaon/Gurugram, Noida, Hyderabad, Pune, Mumbai
        loc_score = 0
        is_target_city = any(c in loc_lower for c in self.profile.target_cities)
        is_bengaluru = self.target_location in loc_lower or "bangalore" in loc_lower

        if not location:
            loc_score = 4
            reasons.append("Location not specified (+4)")
        elif is_bengaluru:
            loc_score = self.weights.location_fit_weight
            reasons.append(f"Primary target location match ({location}) (+{loc_score})")
        elif "remote" in loc_lower or "anywhere" in loc_lower:
            loc_score = int(self.weights.location_fit_weight * 0.8)  # 12 pts
            reasons.append(f"Remote opportunity (+{loc_score})")
        elif is_target_city or "india" in loc_lower:
            loc_score = int(self.weights.location_fit_weight * 0.9)  # 13 or 14 pts for other major India hubs
            reasons.append(f"Target India tech hub match ({location}) (+{loc_score})")
        else:
            loc_score = 0
            reasons.append(f"Location mismatch: outside target tech hubs '{location}' (+0)")

        breakdown["location_fit"] = loc_score

        # 5. Evidence Confidence & Available Fields Tracking
        available_fields: List[str] = ["title", "company"]
        unverified_components: List[str] = []

        if location and location.strip():
            available_fields.append("location")
        else:
            unverified_components.append("location")

        has_description = bool(description and len(description.strip()) >= 50)
        if has_description:
            available_fields.append("description")
        else:
            unverified_components.append("skills_description")

        if exp_level != ExperienceLevel.UNKNOWN:
            available_fields.append("experience_requirements")
        else:
            unverified_components.append("experience_requirements")

        # Determine Confidence Level & Confidence Score
        is_provisional = not has_description
        if has_description and exp_level != ExperienceLevel.UNKNOWN and len(matched_skills) >= 2:
            confidence_level = ConfidenceLevel.HIGH
            confidence_score = 0.9
        elif has_description or (len(matched_skills) >= 1 and exp_level != ExperienceLevel.UNKNOWN):
            confidence_level = ConfidenceLevel.MEDIUM
            confidence_score = 0.6
        else:
            confidence_level = ConfidenceLevel.LOW
            confidence_score = 0.25

        if is_provisional:
            reasons.append("LOW CONFIDENCE: Description missing; score is provisional based on search-card metadata.")

        # Total Calculation clamped between 0 and 100
        raw_total = sum(breakdown.values())
        total_score = max(0, min(100, raw_total))

        # 6. Establish Fit Eligibility Separately from Score
        # Hard Rule from Requirements: Requirements above 4 years must be excluded from eligible recommendations (LOW_FIT)
        has_primary_backend_framework = any(s in matched_skills for s in [
            "spring boot", "spring / spring mvc", "microservices", "rest api", "hibernate / jpa", "sql / postgresql / mysql"
        ])

        if deterministic_eligibility is not None:
            if deterministic_eligibility.decision == DeterministicDecision.REJECTED:
                eligibility = FitEligibility.LOW_FIT
                elig_reason = f"Deterministic rejection: {'; '.join(deterministic_eligibility.rejection_reasons)}"
            elif deterministic_eligibility.decision == DeterministicDecision.NEEDS_REVIEW:
                eligibility = FitEligibility.PROVISIONAL_NEEDS_REVIEW
                elig_reason = f"Deterministic review required: {'; '.join(deterministic_eligibility.review_reasons)}"
            elif exp_eligibility == ExperienceEligibility.OVER_LIMIT:
                eligibility = FitEligibility.LOW_FIT
                elig_reason = f"Experience requirement exceeds hard limit ({exp_reason}); excluded from eligible recommendations."
            elif is_provisional:
                eligibility = FitEligibility.PROVISIONAL_NEEDS_REVIEW
                elig_reason = "Description missing/unretrieved; requires review of full job posting."
            elif total_score >= 60 and len(matched_skills) >= 2 and has_primary_backend_framework and exp_score >= 10:
                eligibility = FitEligibility.HIGH_FIT
                elig_reason = f"Strong match: verified description, {len(matched_skills)} confirmed skills including backend frameworks, compatible experience fit."
            elif total_score >= 50 and len(matched_skills) >= 1 and exp_score >= 6:
                eligibility = FitEligibility.MEDIUM_FIT
                elig_reason = f"Moderate match: partial framework coverage or experience stretch ({exp_level.value})."
            else:
                eligibility = FitEligibility.LOW_FIT
                elig_reason = f"Low fit: insufficient skills evidence ({len(matched_skills)} skills) or experience mismatch."
        elif exp_eligibility == ExperienceEligibility.OVER_LIMIT:
            eligibility = FitEligibility.LOW_FIT
            elig_reason = f"Experience requirement exceeds 4-year limit ({exp_reason}); excluded from eligible recommendations."
        elif is_provisional:
            eligibility = FitEligibility.PROVISIONAL_NEEDS_REVIEW
            elig_reason = "Description missing/unretrieved; requires review of full job posting."
        elif total_score >= 60 and len(matched_skills) >= 2 and has_primary_backend_framework and exp_score >= 10:
            eligibility = FitEligibility.HIGH_FIT
            elig_reason = f"Strong match: verified description, {len(matched_skills)} confirmed skills including backend frameworks, compatible experience fit."
        elif total_score >= 50 and len(matched_skills) >= 1 and exp_score >= 6:
            eligibility = FitEligibility.MEDIUM_FIT
            elig_reason = f"Moderate match: partial framework coverage or experience stretch ({exp_level.value})."
        else:
            eligibility = FitEligibility.LOW_FIT
            elig_reason = f"Low fit: insufficient skills evidence ({len(matched_skills)} skills) or experience mismatch."

        return RelevanceScore(
            total_score=total_score,
            experience_level=exp_level,
            experience_eligibility=exp_eligibility,
            confidence_level=confidence_level,
            confidence_score=confidence_score,
            eligibility=eligibility,
            eligibility_reason=elig_reason,
            is_provisional=is_provisional,
            unverified_components=unverified_components,
            available_fields=available_fields,
            score_breakdown=breakdown,
            reasons=reasons,
            matched_skills=matched_skills,
            missing_critical_skills=missing_critical_skills
        )

