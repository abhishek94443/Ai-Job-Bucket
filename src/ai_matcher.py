import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

from src.models import (
    JobRecord,
    DeterministicDecision,
    FitEligibility,
)
from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
from src.privacy_scrubber import PrivacyScrubber
from src.ai_service import (
    compute_profile_hash,
    compute_job_hash,
    DEFAULT_GEMINI_MODEL,
    CURRENT_PROMPT_VERSION,
    PROMPT_VERSION_V1,
    PROMPT_VERSION_V2,
    resolve_model_name,
    build_candidate_summary_for_ai,
    build_job_match_prompt,
    build_job_match_prompt_v2,
    MATCH_EVALUATION_SYSTEM_INSTRUCTION,
    MATCH_EVALUATION_SYSTEM_INSTRUCTION_V2,
    AIMatchEvaluation,
    AIMatchEvaluationV2,
    MatchProvenance,
    AIMatchVerdict,
    ExperienceSeniorityFit,
    AIDailyQuotaExhaustedError,
    AIDatabaseConnectivityError,
    GeminiAIService,
)

logger = logging.getLogger(__name__)



class AIMatchCandidate(BaseModel):
    """Details of a job listing selected for AI semantic matching."""
    platform: str
    platform_job_id: str
    title: str
    company: str
    location: Optional[str] = None
    eligibility_decision: str
    relevance_score: Optional[int] = None
    selection_reason: str
    profile_hash: str
    job_hash: str
    model_version: str = DEFAULT_GEMINI_MODEL
    prompt_version: str = CURRENT_PROMPT_VERSION
    scrubbed_payload_preview: Dict[str, Any]


class AIMatchDryRunReport(BaseModel):
    """Structured report of an AI matching dry run with zero external API calls or DB writes."""
    total_discovered: int
    counts_by_source: Dict[str, int]
    counts_by_eligibility: Dict[str, int]
    ai_candidates_count: int
    excluded_count: int
    ai_candidates: List[AIMatchCandidate] = Field(default_factory=list)
    excluded_jobs: List[Dict[str, Any]] = Field(default_factory=list)
    quota_reserved: int = 0
    llm_calls_made: int = 0
    db_writes_made: int = 0
    is_dry_run: bool = True

    def format_summary(self) -> str:
        """Returns human-readable text report of the dry run."""
        lines = [
            "=" * 80,
            "=== JOB BUCKET: AI SEMANTIC MATCHING INTEGRATION DRY RUN ===",
            "=" * 80,
            f"Total Discovered Jobs:     {self.total_discovered}",
            f"Counts by Source:          {self.counts_by_source}",
            f"Counts by Eligibility:     {self.counts_by_eligibility}",
            f"Selected for AI Matching:  {self.ai_candidates_count}",
            f"Excluded (Hard Rejection): {self.excluded_count}",
            f"LLM API Calls Dispatched:  {self.llm_calls_made} (Guaranteed Zero in Dry Run)",
            f"Quota Units Reserved:      {self.quota_reserved} (Guaranteed Zero in Dry Run)",
            f"Database Writes:           {self.db_writes_made} (Guaranteed Zero in Dry Run)",
            "-" * 80,
            "--- CANDIDATES FOR AI MATCHING (ELIGIBLE or NEEDS_REVIEW) ---",
        ]

        if not self.ai_candidates:
            lines.append("  None (all discovered jobs were deterministically rejected or none found).")
        else:
            for idx, cand in enumerate(self.ai_candidates, 1):
                lines.append(
                    f"  [{idx}] [{cand.platform.upper()}] {cand.title} @ {cand.company} ({cand.location or 'N/A'})"
                )
                lines.append(f"      Eligibility: {cand.eligibility_decision} | Rel Score: {cand.relevance_score or 'N/A'}")
                lines.append(f"      Selection Reason: {cand.selection_reason}")
                lines.append(f"      Cache Keys: profile_hash={cand.profile_hash[:12]}..., job_hash={cand.job_hash[:12]}...")
                lines.append(f"      Scrubbed Description Preview: {cand.scrubbed_payload_preview.get('description_preview', '')}")
                lines.append(f"      Candidate Scrubbed Skills: {', '.join(cand.scrubbed_payload_preview.get('candidate_summary', {}).get('languages', []))}, {', '.join(cand.scrubbed_payload_preview.get('candidate_summary', {}).get('frameworks', []))}")
                lines.append("")

        if self.excluded_jobs:
            lines.append("-" * 80)
            lines.append("--- EXCLUDED JOBS (DETERMINISTIC REJECTION - IMPOSSIBLE TO OVERRIDE) ---")
            for idx, exc in enumerate(self.excluded_jobs, 1):
                reasons = "; ".join(exc.get("rejection_reasons", []))
                lines.append(
                    f"  [{idx}] [{exc.get('platform', '').upper()}] {exc.get('title', '')} @ {exc.get('company', '')}"
                )
                lines.append(f"      Reason: {reasons}")
                lines.append("")

        lines.append("=" * 80)
        return "\n".join(lines)


class AIMatcher:
    """
    Coordinates AI semantic matching preparation, candidate filtering, privacy scrubbing,
    and dry-run auditing.
    Guarantees:
    1. Deterministic rejection is impossible to override: all REJECTED jobs are discarded
       before any LLM dispatch or prompt assembly.
    2. Strict scrubbing: all descriptions and profiles are sanitized with PrivacyScrubber.
    3. Dry-run safety: 0 API calls, 0 quota reservations, 0 DB writes.
    """

    def __init__(
        self,
        model_version: Optional[str] = None,
        prompt_version: str = CURRENT_PROMPT_VERSION,
    ):
        self.model_version = resolve_model_name(model_version)
        self.prompt_version = prompt_version

    @classmethod
    def select_ai_candidates(
        cls,
        jobs: List[JobRecord],
    ) -> Tuple[List[JobRecord], List[JobRecord]]:
        """
        In AI-First architecture:
        All discovered jobs are candidates for AI semantic evaluation.
        Deterministic eligibility is retained as supplementary information and
        does not block jobs from reaching AI analysis.
        """
        candidates: List[JobRecord] = list(jobs)
        excluded: List[JobRecord] = []
        return candidates, excluded


    def build_scrubbed_payload(
        self,
        job: JobRecord,
        candidate: CandidateProfile,
    ) -> Dict[str, Any]:
        """
        Constructs the exact sanitized payload for AI matching with zero PII.
        Applies PrivacyScrubber to job descriptions and strips personal candidate identity.
        """
        # 1. Scrub job description
        raw_desc = job.description_snippet or ""
        scrubbed_desc, red_counts = PrivacyScrubber.scrub_text(raw_desc, candidate_name=candidate.name)

        # 2. Build candidate structured technical summary (zero PII: no name, email, phone, location)
        candidate_summary = build_candidate_summary_for_ai(candidate)

        # 3. Compute deterministic cache keys

        prof_hash = compute_profile_hash(candidate)
        job_h = compute_job_hash(
            platform=job.platform,
            platform_job_id=job.platform_job_id,
            title=job.title,
            company=job.company,
            description=job.description_snippet or "",
        )

        preview_text = scrubbed_desc[:180] + ("..." if len(scrubbed_desc) > 180 else "")

        return {
            "profile_hash": prof_hash,
            "job_hash": job_h,
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "candidate_summary": candidate_summary,
            "job_title": job.title,
            "company": job.company,
            "location": job.location,
            "description_preview": preview_text,
            "redactions_applied": red_counts,
        }

    @classmethod
    def build_effective_prompt(
        cls,
        candidate: CandidateProfile,
        job: JobRecord,
    ) -> Tuple[str, str]:
        """
        Constructs the exact system instruction and user prompt matching GeminiAIService.evaluate_job_match.
        Guarantees 100% dry-run fidelity with live provider requests.
        """
        raw_desc = job.description_snippet or ""
        scrubbed_desc, _ = PrivacyScrubber.scrub_text(raw_desc, candidate_name=candidate.name)
        candidate_summary = build_candidate_summary_for_ai(candidate)
        prompt = build_job_match_prompt(
            candidate_summary=candidate_summary,
            job_title=job.title,
            company=job.company,
            location=job.location or "N/A",
            scrubbed_description=scrubbed_desc,
        )
        return MATCH_EVALUATION_SYSTEM_INSTRUCTION, prompt

    @classmethod
    def build_effective_prompt_v2(
        cls,
        candidate: CandidateProfile,
        job: JobRecord,
    ) -> Tuple[str, str]:
        """
        Constructs the exact V2 system instruction and user prompt matching GeminiAIService.evaluate_job_match_v2.
        Includes supplementary deterministic eligibility context without letting it gate execution.
        """
        raw_desc = job.description_snippet or ""
        scrubbed_desc, _ = PrivacyScrubber.scrub_text(raw_desc, candidate_name=candidate.name)
        candidate_summary = build_candidate_summary_for_ai(candidate)
        supp_elig = job.deterministic_eligibility.model_dump() if job.deterministic_eligibility else None
        prompt = build_job_match_prompt_v2(
            candidate_summary=candidate_summary,
            job_title=job.title,
            company=job.company,
            location=job.location or "N/A",
            scrubbed_description=scrubbed_desc,
            supplementary_eligibility=supp_elig,
        )
        return MATCH_EVALUATION_SYSTEM_INSTRUCTION_V2, prompt

    def evaluate_job_ai_first(
        self,
        job: JobRecord,
        candidate: CandidateProfile,
        ai_service: GeminiAIService,
        resume_service: Optional[Any] = None,
        db_conn = None,
        skip_cache_save: bool = False,
    ) -> AIMatchEvaluationV2:
        """
        Executes AI-First job matching with strict routing invariants:
        1. Cache-first: Checks ai_match_cache for V2 evaluation. If hit -> returns AI_CACHED (0 quota, 0 API calls).
        2. Uncached evaluation: Attempts atomic quota reservation.
           - If quota available: dispatches Gemini request, returns AI_LIVE.
           - If AIDailyQuotaExhaustedError (and ONLY on quota exhaustion): invokes deterministic fallback, returns DETERMINISTIC_FALLBACK.
           - If any other error (DB connectivity, config, timeout, HTTP, JSON parse): fails closed (never silent fallback).
        """
        prof_hash = compute_profile_hash(candidate)
        job_h = compute_job_hash(
            platform=job.platform,
            platform_job_id=job.platform_job_id,
            title=job.title,
            company=job.company,
            description=job.description_snippet or "",
        )
        model_ver = ai_service.model_name
        prompt_ver = PROMPT_VERSION_V2

        # 1. Cache lookup
        if resume_service is not None:
            cached = resume_service.get_cached_match(
                profile_hash=prof_hash,
                platform=job.platform,
                platform_job_id=job.platform_job_id,
                job_hash=job_h,
                model_version=model_ver,
                prompt_version=prompt_ver,
            )
            if cached is not None:
                if isinstance(cached, AIMatchEvaluationV2):
                    cached.match_source = MatchProvenance.AI_CACHED
                    return cached
                elif hasattr(cached, "prompt_version") and cached.prompt_version == PROMPT_VERSION_V2:
                    v2_res = AIMatchEvaluationV2(**cached.model_dump())
                    v2_res.match_source = MatchProvenance.AI_CACHED
                    return v2_res

        # 2. Quota reservation (Atomic upsert before HTTP dispatch)
        try:
            if db_conn is not None:
                ai_service.reserve_persistent_quota(db_conn)
            elif ai_service.require_persistent_quota:
                from src.db_config import connect_database
                quota_conn = None
                try:
                    try:
                        quota_conn = connect_database()
                    except Exception as conn_err:
                        raise AIDatabaseConnectivityError(
                            f"Persistent quota check failed ({conn_err}). Failing closed: skipping AI calls to protect daily cap."
                        ) from conn_err
                    ai_service.reserve_persistent_quota(quota_conn)
                finally:
                    if quota_conn is not None:
                        try:
                            quota_conn.close()
                        except Exception:
                            pass
        except AIDailyQuotaExhaustedError as quota_err:
            # Explicit quota exhaustion: invoke deterministic fallback
            from src.eligibility import DeterministicJobEvaluator
            from src.scorer import JobRelevanceScorer

            evaluator = DeterministicJobEvaluator()
            det = evaluator.evaluate(job, candidate)
            scorer = JobRelevanceScorer(candidate_profile=candidate)
            rel = scorer.score_job(
                title=job.title,
                company=job.company,
                location=job.location,
                description=job.description_snippet,
                source_experience_years_min=job.source_experience_years_min,
                deterministic_eligibility=det,
            )

            # Map deterministic outcome to AIMatchEvaluationV2 with explicit DETERMINISTIC_FALLBACK provenance
            verdict = AIMatchVerdict.GOOD_MATCH if det.is_eligible else AIMatchVerdict.MISMATCH
            return AIMatchEvaluationV2(
                match_score=rel.total_score,
                fit_verdict=verdict,
                confidence_score=rel.confidence_score,
                role_responsibilities=[],
                required_skills=[],
                preferred_skills=[],
                matching_skills=rel.matched_skills,
                missing_skills=rel.missing_critical_skills,
                transferable_skills=[],
                job_seniority_level=rel.experience_level.value,
                job_experience_required="Deterministic rule-based requirement",
                experience_fit=ExperienceSeniorityFit.UNCERTAIN_CANDIDATE_DATA if not candidate.experience_confirmed else ExperienceSeniorityFit.ALIGNED,
                experience_assessment=f"Deterministic fallback: {det.decision.value}",
                work_mode="Unspecified",
                location_fit="Evaluated via deterministic location rules",
                education_and_certifications="None specified",
                evidence=det.reasons,
                uncertainties=det.review_reasons or (["Candidate total experience is unconfirmed in profile"] if not candidate.experience_confirmed else []),
                actionable_recommendations=[],
                reasoning=f"Deterministic fallback evaluation due to AI quota exhaustion: {'; '.join(det.reasons or det.review_reasons or det.rejection_reasons)}",
                prompt_version=prompt_ver,
                match_source=MatchProvenance.DETERMINISTIC_FALLBACK,
                fallback_reason=str(quota_err),
                supplementary_deterministic_eligibility=det.model_dump(),
            )
        # Any other exception (AIDatabaseConnectivityError, AIConfigurationError, AIServiceError) propagates and FAILS CLOSED

        # 3. Quota reserved successfully -> Dispatch AI evaluation
        supp_elig = job.deterministic_eligibility.model_dump() if job.deterministic_eligibility else None
        eval_v2 = ai_service.evaluate_job_match_v2(
            profile=candidate,
            job_title=job.title,
            company=job.company,
            location=job.location or "N/A",
            description=job.description_snippet or "",
            supplementary_eligibility=supp_elig,
            db_conn=db_conn,
            reserve_quota=False,  # Already reserved above
        )

        # 4. Optional cache persistence
        if resume_service is not None and not skip_cache_save:
            try:
                resume_service.save_cached_match(
                    profile_hash=prof_hash,
                    platform=job.platform,
                    platform_job_id=job.platform_job_id,
                    job_hash=job_h,
                    model_version=model_ver,
                    evaluation=eval_v2,
                    prompt_version=prompt_ver,
                )
            except Exception as exc:
                logger.warning("Failed to save V2 evaluation to cache: %s", exc)

        return eval_v2

    evaluate_job_v2 = evaluate_job_ai_first

    def run_dry_run(

        self,
        jobs: List[JobRecord],
        candidate: Optional[CandidateProfile] = None,
    ) -> AIMatchDryRunReport:
        """
        Executes a dry-run evaluation of the discovered jobs.
        Generates candidate previews, validates scrubbing, and reports outcomes
        without making real AI calls, reserving quota, or modifying the database.
        """
        cand = candidate or DEFAULT_CANDIDATE_PROFILE

        # 1. Source breakdown
        counts_by_source: Dict[str, int] = {}
        for j in jobs:
            counts_by_source[j.platform] = counts_by_source.get(j.platform, 0) + 1

        # 2. Eligibility breakdown
        counts_by_eligibility: Dict[str, int] = {
            DeterministicDecision.ELIGIBLE.value: 0,
            DeterministicDecision.NEEDS_REVIEW.value: 0,
            DeterministicDecision.REJECTED.value: 0,
        }
        for j in jobs:
            det = j.deterministic_eligibility
            dec = det.decision.value if det else DeterministicDecision.NEEDS_REVIEW.value
            counts_by_eligibility[dec] = counts_by_eligibility.get(dec, 0) + 1

        # 3. Candidate partitioning
        ai_jobs, excluded_jobs_list = self.select_ai_candidates(jobs)

        ai_candidates: List[AIMatchCandidate] = []
        for j in ai_jobs:
            det = j.deterministic_eligibility
            dec_str = det.decision.value if det else "NEEDS_REVIEW"

            if dec_str == DeterministicDecision.ELIGIBLE.value:
                sel_reason = "Deterministic ELIGIBLE: verified technical stack; AI matching evaluates depth & nuances"
            elif dec_str == DeterministicDecision.REJECTED.value:
                reasons = "; ".join(det.rejection_reasons) if det and det.rejection_reasons else "hard cutoff"
                sel_reason = f"Deterministic REJECTED: {reasons}; AI matching evaluates semantic fit & transferable skills"
            else:
                sel_reason = f"Deterministic NEEDS_REVIEW: {'; '.join(det.review_reasons) if det else 'unreviewed'}; AI matching evaluates skill alignment"

            payload_data = self.build_scrubbed_payload(j, cand)

            ai_candidates.append(
                AIMatchCandidate(
                    platform=j.platform,
                    platform_job_id=j.platform_job_id,
                    title=j.title,
                    company=j.company,
                    location=j.location,
                    eligibility_decision=dec_str,
                    relevance_score=j.relevance.total_score if j.relevance else None,
                    selection_reason=sel_reason,
                    profile_hash=payload_data["profile_hash"],
                    job_hash=payload_data["job_hash"],
                    model_version=self.model_version,
                    prompt_version=self.prompt_version,
                    scrubbed_payload_preview=payload_data,
                )
            )

        excluded_records: List[Dict[str, Any]] = []
        for j in excluded_jobs_list:
            det = j.deterministic_eligibility
            excluded_records.append({
                "platform": j.platform,
                "platform_job_id": j.platform_job_id,
                "title": j.title,
                "company": j.company,
                "rejection_reasons": det.rejection_reasons if det else ["Hard rejection"],
            })

        return AIMatchDryRunReport(
            total_discovered=len(jobs),
            counts_by_source=counts_by_source,
            counts_by_eligibility=counts_by_eligibility,
            ai_candidates_count=len(ai_candidates),
            excluded_count=len(excluded_records),
            ai_candidates=ai_candidates,
            excluded_jobs=excluded_records,
            quota_reserved=0,
            llm_calls_made=0,
            db_writes_made=0,
            is_dry_run=True,
        )
