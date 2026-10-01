import re
import logging
from typing import Optional, List, Dict, Any, Tuple

from .models import (
    JobRecord,
    DeterministicDecision,
    DeterministicEligibility,
    ExperienceLevel,
    ExperienceEligibility,
)
from .candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
from .experience_classifier import ExperienceClassifier

logger = logging.getLogger(__name__)


class DeterministicJobEvaluator:
    """
    Evaluates discovered jobs against candidate profile facts and constraints deterministically.
    
    Principles:
    1. Zero Hallucinations: Never treat missing job experience as 0. Never treat unconfirmed candidate experience as confirmed.
    2. Strict Evidence Threshold: Only reject when supported by reliable evidence and candidate constraint is known.
    3. Explainable Outcomes: Returns ELIGIBLE, NEEDS_REVIEW, or REJECTED with explicit reasons and breakdown.
    4. Safety Invariant: Hard rejection cannot be overridden by semantic or LLM similarity scores.
    """

    NEGATIVE_ROLE_KEYWORDS = [
        "frontend", "front-end", "react", "angular", "vue", "ios", "android",
        "flutter", "qa engineer", "data scientist", "data analyst", "hr manager",
        "graphic designer", "accountant", "cfo", "marketing"
    ]

    FOREIGN_EXCLUDED_CITIES = [
        "tokyo", "london", "singapore", "usa", "uk", "germany", "dubai",
        "japan", "berlin", "taiwan", "sydney", "toronto", "new york", "san francisco"
    ]

    def evaluate(
        self,
        job: JobRecord,
        candidate: Optional[CandidateProfile] = None,
    ) -> DeterministicEligibility:
        cand = candidate or DEFAULT_CANDIDATE_PROFILE
        reasons: List[str] = []
        rejection_reasons: List[str] = []
        review_reasons: List[str] = []
        criteria_breakdown: Dict[str, str] = {}

        # ------------------------------------------------------------------
        # 1. Experience Criteria Evaluation
        # ------------------------------------------------------------------
        exp_status, exp_reasons, exp_rejections, exp_reviews = self._evaluate_experience(job, cand)
        criteria_breakdown["experience"] = exp_status
        reasons.extend(exp_reasons)
        rejection_reasons.extend(exp_rejections)
        review_reasons.extend(exp_reviews)

        # ------------------------------------------------------------------
        # 2. Location & Remote Work Format Evaluation
        # ------------------------------------------------------------------
        loc_status, loc_reasons, loc_rejections, loc_reviews = self._evaluate_location_and_remote(job, cand)
        criteria_breakdown["location_and_remote"] = loc_status
        reasons.extend(loc_reasons)
        rejection_reasons.extend(loc_rejections)
        review_reasons.extend(loc_reviews)

        # ------------------------------------------------------------------
        # 3. Role Domain & Title Compatibility Evaluation
        # ------------------------------------------------------------------
        role_status, role_reasons, role_rejections, role_reviews = self._evaluate_role_domain(job, cand)
        criteria_breakdown["role_domain"] = role_status
        reasons.extend(role_reasons)
        rejection_reasons.extend(role_rejections)
        review_reasons.extend(role_reviews)

        # ------------------------------------------------------------------
        # 4. Final Deterministic Decision Synthesis
        # ------------------------------------------------------------------
        if rejection_reasons:
            decision = DeterministicDecision.REJECTED
            is_eligible = False
        elif review_reasons:
            decision = DeterministicDecision.NEEDS_REVIEW
            is_eligible = True  # Provisional eligibility pending review
        else:
            decision = DeterministicDecision.ELIGIBLE
            is_eligible = True

        return DeterministicEligibility(
            decision=decision,
            is_eligible=is_eligible,
            reasons=reasons,
            rejection_reasons=rejection_reasons,
            review_reasons=review_reasons,
            criteria_breakdown=criteria_breakdown,
        )

    def _evaluate_experience(
        self,
        job: JobRecord,
        candidate: CandidateProfile,
    ) -> Tuple[str, List[str], List[str], List[str]]:
        reasons: List[str] = []
        rejections: List[str] = []
        reviews: List[str] = []

        # Candidate profile parameters
        cand_exp = candidate.total_experience_years
        cand_confirmed = candidate.experience_confirmed and (cand_exp is not None)
        hard_limit = candidate.hard_limit_years
        range_max = candidate.experience_range_max or 3.0

        # Job experience extraction:
        # A) Explicit structured field (e.g. Freehire source_experience_years_min)
        # B) Extracted numeric year ranges from text
        full_text = f"{job.title}\n{job.description_snippet or ''}"
        ranges = ExperienceClassifier.extract_years_requirement(full_text)

        source_exp_min = float(job.source_experience_years_min) if job.source_experience_years_min is not None else None

        # Check for contradictory numerical ranges (e.g. 1-2 years AND 8-10 years in the same listing)
        if len(ranges) >= 2:
            min_all = min(r[0] for r in ranges)
            max_all = max(r[1] for r in ranges)
            if min_all <= 2.0 and max_all >= 7.0:
                reviews.append(f"Contradictory experience requirements in text: {ranges}; needs review.")
                return "NEEDS_REVIEW", reasons, rejections, reviews

        # Resolve primary job requirement
        job_min: Optional[float] = None
        job_max: Optional[float] = None

        if source_exp_min is not None:
            job_min = source_exp_min
            job_max = ranges[0][1] if ranges else (source_exp_min + 2.0)
        elif ranges:
            job_min, job_max = ranges[0]

        # Case 1: Job experience is known
        if job_min is not None:
            # Check hard limit rejection:
            # Hard rejection applies if and only if the job's minimum requirement strictly exceeds the candidate's hard ceiling
            if hard_limit is not None and job_min > hard_limit:
                source_note = "Structured source requirement" if source_exp_min is not None else "Job requirement"
                rejections.append(
                    f"{source_note}: requires minimum {job_min:g}+ years of experience, exceeding candidate hard cutoff of {hard_limit:g} years."
                )
                return "REJECTED", reasons, rejections, reviews

            # If candidate experience is unknown or unconfirmed:
            # Rule: Never treat unknown candidate experience as confirmed.
            if not cand_confirmed:
                reviews.append(
                    f"Job requires {job_min:g}+ years of experience; candidate professional experience is unconfirmed or unknown."
                )
                return "NEEDS_REVIEW", reasons, rejections, reviews

            # Candidate experience is confirmed!
            # If job minimum is within candidate confirmed experience or normal entry threshold:
            if job_min <= (cand_exp + 1.0) and job_min <= range_max:
                if hard_limit is not None and job_max > hard_limit:
                    reviews.append(
                        f"Job experience range ({job_min:g}-{job_max:g} YOE) spans candidate limit ({hard_limit:g} YOE); potential stretch requiring review."
                    )
                    return "NEEDS_REVIEW", reasons, rejections, reviews
                reasons.append(
                    f"Experience compatible: job requires {job_min:g}-{job_max:g} YOE; candidate has {cand_exp:g} confirmed YOE."
                )
                return "ELIGIBLE", reasons, rejections, reviews

            # If job minimum is a stretch (e.g. 4 years for 2 YOE candidate, or range 2-5 years)
            if job_min <= (hard_limit or 4.0):
                reviews.append(
                    f"Job requires {job_min:g} YOE (moderate stretch for candidate's {cand_exp:g} YOE, within {hard_limit:g} YOE cutoff); review needed."
                )
                return "NEEDS_REVIEW", reasons, rejections, reviews

            # Exceeds confirmed experience and cutoff
            rejections.append(
                f"Job requires {job_min:g} YOE, exceeding candidate experience ({cand_exp:g} YOE) and preference."
            )
            return "REJECTED", reasons, rejections, reviews

        # Case 2: Job experience is missing / not specified
        # Check title cues without numbers:
        t_lower = (job.title or "").lower()
        if any(w in t_lower for w in ["senior", "sr.", "sr ", "lead", "principal", "architect"]):
            # Rule: Do not invent job requirements from titles. Return needs-review rather than unsupported rejection.
            reviews.append(
                "Job title suggests Senior/Lead role without explicit numeric experience (ambiguous requirement); needs review."
            )
            return "NEEDS_REVIEW", reasons, rejections, reviews

        # Missing experience entirely: Rule: Never treat missing job experience as zero.
        reviews.append(
            "Job posting does not specify experience requirements (unspecified or unretrieved); retained for review (never treated as zero)."
        )
        return "NEEDS_REVIEW", reasons, rejections, reviews

    def _evaluate_location_and_remote(
        self,
        job: JobRecord,
        candidate: CandidateProfile,
    ) -> Tuple[str, List[str], List[str], List[str]]:
        reasons: List[str] = []
        rejections: List[str] = []
        reviews: List[str] = []

        job_loc = (job.location or "").lower().strip()
        work_mode = (job.work_mode or "").lower().strip()
        remote_pref = (candidate.remote_preference or "flexible").lower()
        target_cities = [c.lower() for c in candidate.target_cities]
        if not target_cities:
            target_cities = [
                "bengaluru", "bangalore", "delhi", "new delhi", "gurgaon", "gurugram",
                "noida", "hyderabad", "pune", "mumbai"
            ]

        # Case 1: Candidate preference is remote_only or remote
        if remote_pref in ("remote_only", "remote"):
            if work_mode == "remote" or "remote" in job_loc or "remote" in (job.title or "").lower():
                reasons.append("Remote job matches candidate remote work preference.")
                return "ELIGIBLE", reasons, rejections, reviews
            elif work_mode in ("onsite", "hybrid"):
                rejections.append(
                    f"Candidate configured remote work preference, but job requires {work_mode} work."
                )
                return "REJECTED", reasons, rejections, reviews
            elif not job_loc:
                reviews.append("Job location and work format unstated; needs review against remote work preference.")
                return "NEEDS_REVIEW", reasons, rejections, reviews
            else:
                rejections.append(
                    f"Candidate configured remote work preference, but job is located at '{job.location}' and not identified as remote."
                )
                return "REJECTED", reasons, rejections, reviews

        # Case 2: Candidate preference is flexible, hybrid, or onsite
        # Check if remote
        if "remote" in job_loc or work_mode == "remote":
            reasons.append("Remote work format is compatible with candidate preferences.")
            return "ELIGIBLE", reasons, rejections, reviews

        # If location is missing
        if not job_loc:
            reviews.append("Job location not specified; retained for review.")
            return "NEEDS_REVIEW", reasons, rejections, reviews

        # Check target cities
        is_target_city = any(c in job_loc for c in target_cities)
        if is_target_city:
            reasons.append(f"Job location '{job.location}' matches candidate target tech hub.")
            return "ELIGIBLE", reasons, rejections, reviews

        # Explicit foreign location mismatch (e.g. Tokyo, London) with no remote option
        is_foreign = any(foreign in job_loc for foreign in self.FOREIGN_EXCLUDED_CITIES)
        if is_foreign and work_mode != "remote" and "remote" not in job_loc:
            rejections.append(
                f"Job location '{job.location}' is outside candidate target geography and is not remote."
            )
            return "REJECTED", reasons, rejections, reviews

        # Check general India tech hub
        if "india" in job_loc:
            reasons.append(f"Job location '{job.location}' is within India; compatible with candidate preferences.")
            return "ELIGIBLE", reasons, rejections, reviews

        reviews.append(f"Job location '{job.location}' is outside primary target hubs; needs review.")
        return "NEEDS_REVIEW", reasons, rejections, reviews

    def _evaluate_role_domain(
        self,
        job: JobRecord,
        candidate: CandidateProfile,
    ) -> Tuple[str, List[str], List[str], List[str]]:
        reasons: List[str] = []
        rejections: List[str] = []
        reviews: List[str] = []

        t_lower = (job.title or "").lower()

        # Check if role matches an explicit negative domain
        has_negative = any(re.search(rf"\b{re.escape(k)}\b", t_lower) for k in self.NEGATIVE_ROLE_KEYWORDS)
        has_java_or_backend = any(k in t_lower for k in ["java", "spring", "backend", "back-end", "back end", "microservice"])

        if has_negative and not has_java_or_backend:
            rejections.append(
                f"Role title '{job.title}' conflicts with candidate target role/domain ({candidate.target_title or 'Java Backend'})."
            )
            return "REJECTED", reasons, rejections, reviews

        reasons.append(f"Role title '{job.title}' is compatible with target domain.")
        return "ELIGIBLE", reasons, rejections, reviews
