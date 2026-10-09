"""
Job Bucket — Multi-Source AI Preview Service (Phase 7B).
Provides a controlled, in-memory preview workflow across LinkedIn, Indeed, and Freehire.
Enforces strict bounds:
- Sources: LinkedIn, Indeed, Freehire
- Location: Bengaluru, Posting window: past 24 hours
- Max 2 unique jobs per source, max 6 unique jobs total
- Strict 4 requests / rolling 60-second window rate limiter across concurrent processes/threads
- Preserves daily 50-call UTC quota guardrail
- Zero persistence to jobs table, zero cache writes, zero file exports
- Full-description integrity verification (partial descriptions skip AI evaluation)
- Fail closed on provider errors (no unmonitored fallbacks)
"""

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from src.models import (
    JobRecord,
    FitEligibility,
    FreshnessStatus,
    DeterministicDecision,
    DeterministicEligibility,
    RelevanceScore,
)
from src.candidate_profile import CandidateProfile, DEFAULT_CANDIDATE_PROFILE
from src.discovery import JobDiscoveryService
from src.scorer import JobRelevanceScorer
from src.eligibility import DeterministicJobEvaluator
from src.ai_service import (
    GeminiAIService,
    AIMatchEvaluationV2,
    MatchProvenance,
    AIMatchVerdict,
    AIDailyQuotaExhaustedError,
    AIServiceError,
    DEFAULT_GEMINI_MODEL,
    CURRENT_PROMPT_VERSION,
    compute_profile_hash,
    compute_job_hash,
)
from src.ai_matcher import AIMatcher
from src.rate_limiter import SharedRollingRateLimiter
from src.resume_service import ResumeService

logger = logging.getLogger(__name__)


class DeduplicationOutcome(str, Enum):
    UNIQUE_ACCEPTED = "UNIQUE_ACCEPTED"
    DUPLICATE_SOURCE_SKIPPED = "DUPLICATE_SOURCE_SKIPPED"
    DUPLICATE_CROSS_SOURCE_SKIPPED = "DUPLICATE_CROSS_SOURCE_SKIPPED"
    TOTAL_LIMIT_REACHED = "TOTAL_LIMIT_REACHED"


class EvaluationStatus(str, Enum):
    LIVE = "LIVE"
    CACHED = "CACHED"
    SKIPPED_PARTIAL_DESCRIPTION = "SKIPPED_PARTIAL_DESCRIPTION"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
    SKIPPED_TOTAL_LIMIT = "SKIPPED_TOTAL_LIMIT"
    FALLBACK_QUOTA_EXHAUSTED = "FALLBACK_QUOTA_EXHAUSTED"
    FAILED = "FAILED"


class PreviewConfig(BaseModel):
    """Configuration for controlled preview mode."""
    sources: List[str] = Field(default_factory=lambda: ["linkedin", "indeed", "freehire"])
    location: str = "Bengaluru"
    country: str = "India"
    posted_within_window: str = "24h"
    max_per_source: int = 2
    max_total: int = 6
    max_rpm: int = 4
    rate_limit_window_seconds: float = 60.0
    daily_quota_limit: int = 50
    min_description_chars: int = 250
    skill_queries: Optional[List[str]] = None
    enable_ai_evaluation: bool = True


class PreviewJobItem(BaseModel):
    """Discovered job listing in preview mode with full metadata and evaluation state."""
    source: str
    platform_job_id: str
    title: str
    company: str
    location: Optional[str] = None
    url: str
    posted_date: Optional[str] = None
    is_partial_description: bool
    description_char_count: int
    deduplication_outcome: str
    deterministic_relevance_score: Optional[int] = None
    deterministic_decision: Optional[str] = None
    ai_score: Optional[int] = None
    fit_verdict: Optional[str] = None
    confidence_score: Optional[float] = None
    evaluation_provenance: Optional[str] = None
    evaluation_status: str
    matching_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    transferable_skills: List[str] = Field(default_factory=list)
    reasoning: Optional[str] = None
    uncertainties: List[str] = Field(default_factory=list)
    practical_warnings: List[str] = Field(default_factory=list)
    failure_reason: Optional[str] = None


class PreviewReport(BaseModel):
    """Complete structured preview report for multi-source AI preview run."""
    sources_configured: List[str]
    sources_attempted: List[str]
    sources_succeeded: List[str]
    sources_failed: Dict[str, str] = Field(default_factory=dict)
    total_discovered_raw: int
    total_unique_accepted: int
    total_duplicates_skipped: int
    total_partial_descriptions: int
    total_evaluated_live: int
    total_evaluated_cached: int
    total_evaluations_skipped: int
    total_evaluations_failed: int
    gemini_requests_made: int
    rate_limit_policy: str
    daily_quota_guardrail: str
    is_preview: bool = True
    jobs: List[PreviewJobItem] = Field(default_factory=list)

    def format_text_report(self) -> str:
        """Renders comprehensive markdown report of the preview results."""
        lines = [
            "# Job Bucket — Multi-Source AI Preview Report",
            f"- **Sources Configured**: {', '.join(self.sources_configured)}",
            f"- **Sources Succeeded**: {', '.join(self.sources_succeeded) if self.sources_succeeded else 'None'}",
            f"- **Sources Failed**: {', '.join([f'{k}: {v}' for k, v in self.sources_failed.items()]) if self.sources_failed else 'None'}",
            f"- **Total Discovered (Raw)**: {self.total_discovered_raw}",
            f"- **Unique Jobs Accepted**: {self.total_unique_accepted}",
            f"- **Duplicates Skipped**: {self.total_duplicates_skipped}",
            f"- **Partial Descriptions**: {self.total_partial_descriptions}",
            f"- **AI Evaluations Live**: {self.total_evaluated_live}",
            f"- **AI Evaluations Cached**: {self.total_evaluated_cached}",
            f"- **AI Evaluations Skipped**: {self.total_evaluations_skipped}",
            f"- **AI Evaluations Failed**: {self.total_evaluations_failed}",
            f"- **Gemini API Requests Made**: {self.gemini_requests_made}",
            f"- **Rate Limiting Policy**: {self.rate_limit_policy}",
            f"- **Daily Quota Guardrail**: {self.daily_quota_guardrail}",
            f"- **Persistence Boundary**: Zero DB writes to jobs, zero cache writes, zero file exports",
            "",
            "## Discovered Jobs Breakdown",
            "| # | Source | Title | Company | Status | Description | Det Score | AI Score | Verdict | Provenance |",
            "|---|--------|-------|---------|--------|-------------|-----------|----------|---------|------------|",
        ]

        for idx, j in enumerate(self.jobs, 1):
            desc_status = f"{'Partial' if j.is_partial_description else 'Complete'} ({j.description_char_count} chars)"
            det_disp = str(j.deterministic_relevance_score) if j.deterministic_relevance_score is not None else "-"
            ai_disp = f"{j.ai_score}/100" if j.ai_score is not None else "-"
            verdict_disp = j.fit_verdict or "-"
            prov_disp = j.evaluation_provenance or j.evaluation_status
            lines.append(
                f"| {idx} | {j.source.upper()} | {j.title} | {j.company} | {j.evaluation_status} | {desc_status} | {det_disp} | {ai_disp} | {verdict_disp} | {prov_disp} |"
            )

        lines.append("")
        lines.append("## Detailed Evaluations and Warnings")
        for idx, j in enumerate(self.jobs, 1):
            lines.append(f"### {idx}. [{j.source.upper()}] {j.title} - {j.company}")
            lines.append(f"- **URL**: {j.url}")
            lines.append(f"- **Location**: {j.location or 'N/A'}")
            lines.append(f"- **Posted Date**: {j.posted_date or 'N/A'}")
            lines.append(f"- **Deduplication Outcome**: `{j.deduplication_outcome}`")
            lines.append(f"- **Description Length**: {j.description_char_count} chars ({'PARTIAL' if j.is_partial_description else 'COMPLETE'})")
            lines.append(f"- **Deterministic Score**: {j.deterministic_relevance_score or 'N/A'} (Decision: {j.deterministic_decision or 'N/A'})")
            lines.append(f"- **AI Fit Verdict**: `{j.fit_verdict or 'N/A'}` (Score: {j.ai_score if j.ai_score is not None else 'N/A'}/100, Conf: {j.confidence_score or 'N/A'})")
            lines.append(f"- **Evaluation Status**: `{j.evaluation_status}` (Provenance: `{j.evaluation_provenance or 'N/A'}`)")
            if j.matching_skills:
                lines.append(f"- **Matching Skills**: {', '.join(j.matching_skills)}")
            if j.missing_skills:
                lines.append(f"- **Missing Skills**: {', '.join(j.missing_skills)}")
            if j.transferable_skills:
                lines.append(f"- **Transferable Skills**: {', '.join(j.transferable_skills)}")
            if j.reasoning:
                lines.append(f"- **Reasoning**: {j.reasoning}")
            if j.uncertainties:
                lines.append(f"- **Uncertainties**: {'; '.join(j.uncertainties)}")
            if j.practical_warnings:
                lines.append(f"- **Practical Warnings**: [WARNING] {'; '.join(j.practical_warnings)}")
            if j.failure_reason:
                lines.append(f"- **Failure Details**: [ERROR] {j.failure_reason}")
            lines.append("")

        return "\n".join(lines)


def extract_practical_warnings(job: JobRecord, eval_v2: Optional[AIMatchEvaluationV2] = None) -> List[str]:
    """Generates practical warnings supported strictly by job text and evaluation output."""
    warnings = []
    desc = (job.description_snippet or "").lower()

    if job.is_partial_description or len((job.description_snippet or "").strip()) < 250:
        warnings.append("Incomplete job description (<250 characters); AI evaluation skipped to protect integrity.")

    if any(k in desc for k in ["service agreement", "bond of", "employment bond", "training bond", "2 years bond", "3 years bond"]):
        warnings.append("Service agreement / bond detected in job posting text.")

    if eval_v2 is not None:
        exp_fit = eval_v2.experience_fit
        if exp_fit:
            val = exp_fit.value if hasattr(exp_fit, "value") else str(exp_fit)
            if val in ("POTENTIAL_STRETCH", "SIGNIFICANT_OVERREACH", "OVER_LIMIT"):
                req = eval_v2.job_experience_required or "higher experience"
                warnings.append(f"Experience stretch/gap: role states requirement '{req}'.")
            elif val == "OVER_QUALIFIED":
                warnings.append("Candidate experience may exceed the target seniority of this role.")

    if job.deterministic_eligibility and job.deterministic_eligibility.rejection_reasons:
        for r in job.deterministic_eligibility.rejection_reasons:
            warnings.append(f"Deterministic constraint flag: {r}")

    return warnings


class MultiSourcePreviewService:
    """
    Coordinates multi-source preview discovery across LinkedIn, Indeed, and Freehire.
    Enforces all bounds, description integrity, shared 4-RPM rate limiting,
    and preview persistence boundaries.
    """

    def __init__(
        self,
        config: Optional[PreviewConfig] = None,
        discovery_service: Optional[JobDiscoveryService] = None,
        resume_service: Optional[ResumeService] = None,
        ai_service: Optional[GeminiAIService] = None,
        rate_limiter: Optional[SharedRollingRateLimiter] = None,
        candidate_profile: Optional[CandidateProfile] = None,
    ):
        self.config = config or PreviewConfig()
        self.discovery_service = discovery_service or JobDiscoveryService()
        self.resume_service = resume_service
        self.candidate_profile = candidate_profile

        # Ensure rate limiter is configured
        self.rate_limiter = rate_limiter or SharedRollingRateLimiter(
            max_requests=self.config.max_rpm,
            window_seconds=self.config.rate_limit_window_seconds,
        )

        # Configure AI service with the shared rate limiter
        if ai_service is not None:
            self.ai_service = ai_service
            if self.ai_service.rate_limiter is None:
                self.ai_service.rate_limiter = self.rate_limiter
        else:
            self.ai_service = GeminiAIService(
                daily_limit=self.config.daily_quota_limit,
                rate_limiter=self.rate_limiter,
            )

        self.matcher = AIMatcher(model_version=self.ai_service.model_name)
        self.evaluator = DeterministicJobEvaluator()

    async def run_preview(self) -> PreviewReport:
        """
        Executes bounded preview discovery and evaluation.
        Guarantees:
        - Max 2 unique jobs per source
        - Max 6 unique jobs total
        - Enforces shared 4-RPM rolling window limit
        - Partial descriptions (<250 chars) skip AI evaluation
        - Zero writes to database jobs table
        - Zero writes to AI cache
        - Zero file exports
        """
        # 1. Load active candidate profile
        profile = self.candidate_profile
        if profile is None:
            if self.resume_service is not None:
                try:
                    profile = self.resume_service.get_active_profile()
                except Exception as exc:
                    logger.warning("Could not load active profile via ResumeService: %s", exc)
                    profile = DEFAULT_CANDIDATE_PROFILE
            else:
                profile = DEFAULT_CANDIDATE_PROFILE

        # 2. Select explicit search query (enforce 1 explicit query string to prevent unbounded query expansion)
        query = "Java Developer"
        if self.config.skill_queries and len(self.config.skill_queries) > 0:
            query = self.config.skill_queries[0]
        elif profile.target_title:
            query = profile.target_title
        elif profile.core_search_skill_combinations:
            query = profile.core_search_skill_combinations[0]

        scorer = JobRelevanceScorer(target_location=self.config.location, candidate_profile=profile)

        # 3. Discovery tracking
        attempted_sources: List[str] = []
        succeeded_sources: List[str] = []
        failed_sources: Dict[str, str] = {}
        raw_items_by_source: Dict[str, List[JobRecord]] = {}

        seen_primary_keys: Set[str] = set()
        seen_cross_keys: Set[Tuple[str, str]] = set()

        preview_items: List[PreviewJobItem] = []
        accepted_jobs: List[Tuple[JobRecord, PreviewJobItem]] = []

        total_discovered_raw = 0
        total_duplicates_skipped = 0

        # 4. Multi-source discovery loop
        for src in self.config.sources:
            if len(accepted_jobs) >= self.config.max_total:
                logger.info("Total limit of %d unique jobs reached; halting further source discovery.", self.config.max_total)
                break

            attempted_sources.append(src)
            src_lower = src.lower()
            raw_jobs: List[JobRecord] = []

            try:
                if src_lower == "freehire":
                    logger.info("Preview: Querying Freehire (Bengaluru, last 24h, limit=%d)", self.config.max_per_source)
                    raw_jobs = await self.discovery_service.search_freehire(
                        query=query,
                        country_code="in",
                        cities=self.config.location,
                        posted_within_days=1,
                        limit=self.config.max_per_source,
                        max_pages=1,
                        max_results=self.config.max_per_source,
                    )
                    succeeded_sources.append(src)

                elif src_lower == "linkedin":
                    logger.info("Preview: Querying LinkedIn with detail fetch (Bengaluru, past day, limit=%d)", self.config.max_per_source)
                    raw_jobs = await self.discovery_service.search_linkedin(
                        keyword=query,
                        location=f"{self.config.location}, Karnataka, {self.config.country}",
                        posted_within="Past day",
                        limit=self.config.max_per_source,
                        max_pages=1,
                        max_results=self.config.max_per_source,
                        fetch_details=True,
                    )
                    succeeded_sources.append(src)

                elif src_lower == "indeed":
                    logger.info("Preview: Querying Indeed with detail fetch (Bengaluru, past 24h, limit=%d)", self.config.max_per_source)
                    raw_jobs = await self.discovery_service.search_indeed(
                        keyword=query,
                        location=self.config.location,
                        country=self.config.country,
                        hours_old=24,
                        limit=self.config.max_per_source,
                        max_pages=1,
                        max_results=self.config.max_per_source,
                        fetch_details=True,
                    )
                    succeeded_sources.append(src)

                else:
                    logger.warning("Unknown preview source '%s'; skipping.", src)
                    continue

            except Exception as e:
                logger.error("Error querying source '%s' in preview mode: %s", src, e)
                failed_sources[src] = str(e)
                raw_jobs = []

            total_discovered_raw += len(raw_jobs)
            source_accepted_count = 0

            for job in raw_jobs:
                raw_desc = job.description_snippet or ""
                desc_len = len(raw_desc.strip())
                is_partial = (desc_len < self.config.min_description_chars)
                job.is_partial_description = is_partial

                # Evaluate deterministic eligibility and relevance score
                det = self.evaluator.evaluate(job, profile)
                job.deterministic_eligibility = det
                rel = scorer.score_job(
                    title=job.title,
                    company=job.company,
                    location=job.location,
                    description=job.description_snippet,
                    source_experience_years_min=job.source_experience_years_min,
                    deterministic_eligibility=det,
                )
                job.relevance = rel

                # Check total limit
                if len(accepted_jobs) >= self.config.max_total:
                    total_duplicates_skipped += 1
                    item = PreviewJobItem(
                        source=job.platform,
                        platform_job_id=job.platform_job_id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        url=job.url,
                        posted_date=job.posted_date,
                        is_partial_description=is_partial,
                        description_char_count=desc_len,
                        deduplication_outcome=DeduplicationOutcome.TOTAL_LIMIT_REACHED.value,
                        deterministic_relevance_score=rel.total_score,
                        deterministic_decision=det.decision.value,
                        evaluation_status=EvaluationStatus.SKIPPED_TOTAL_LIMIT.value,
                        practical_warnings=extract_practical_warnings(job),
                    )
                    preview_items.append(item)
                    continue

                # Primary key deduplication within source
                primary_key = f"{job.platform}:{job.platform_job_id}" if job.platform_job_id else job.normalized_url
                if primary_key in seen_primary_keys:
                    total_duplicates_skipped += 1
                    item = PreviewJobItem(
                        source=job.platform,
                        platform_job_id=job.platform_job_id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        url=job.url,
                        posted_date=job.posted_date,
                        is_partial_description=is_partial,
                        description_char_count=desc_len,
                        deduplication_outcome=DeduplicationOutcome.DUPLICATE_SOURCE_SKIPPED.value,
                        deterministic_relevance_score=rel.total_score,
                        deterministic_decision=det.decision.value,
                        evaluation_status=EvaluationStatus.SKIPPED_DUPLICATE.value,
                        practical_warnings=extract_practical_warnings(job),
                    )
                    preview_items.append(item)
                    continue

                # Cross-source deduplication (title + company)
                cross_key = (job.title.strip().lower(), job.company.strip().lower())
                if cross_key in seen_cross_keys:
                    total_duplicates_skipped += 1
                    item = PreviewJobItem(
                        source=job.platform,
                        platform_job_id=job.platform_job_id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        url=job.url,
                        posted_date=job.posted_date,
                        is_partial_description=is_partial,
                        description_char_count=desc_len,
                        deduplication_outcome=DeduplicationOutcome.DUPLICATE_CROSS_SOURCE_SKIPPED.value,
                        deterministic_relevance_score=rel.total_score,
                        deterministic_decision=det.decision.value,
                        evaluation_status=EvaluationStatus.SKIPPED_DUPLICATE.value,
                        practical_warnings=extract_practical_warnings(job),
                    )
                    preview_items.append(item)
                    continue

                # Check per-source limit
                if source_accepted_count >= self.config.max_per_source:
                    total_duplicates_skipped += 1
                    item = PreviewJobItem(
                        source=job.platform,
                        platform_job_id=job.platform_job_id,
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        url=job.url,
                        posted_date=job.posted_date,
                        is_partial_description=is_partial,
                        description_char_count=desc_len,
                        deduplication_outcome=DeduplicationOutcome.DUPLICATE_SOURCE_SKIPPED.value,
                        deterministic_relevance_score=rel.total_score,
                        deterministic_decision=det.decision.value,
                        evaluation_status=EvaluationStatus.SKIPPED_DUPLICATE.value,
                        practical_warnings=extract_practical_warnings(job),
                    )
                    preview_items.append(item)
                    continue

                # Unique job accepted for preview
                seen_primary_keys.add(primary_key)
                seen_cross_keys.add(cross_key)
                source_accepted_count += 1

                item = PreviewJobItem(
                    source=job.platform,
                    platform_job_id=job.platform_job_id,
                    title=job.title,
                    company=job.company,
                    location=job.location,
                    url=job.url,
                    posted_date=job.posted_date,
                    is_partial_description=is_partial,
                    description_char_count=desc_len,
                    deduplication_outcome=DeduplicationOutcome.UNIQUE_ACCEPTED.value,
                    deterministic_relevance_score=rel.total_score,
                    deterministic_decision=det.decision.value,
                    evaluation_status=EvaluationStatus.LIVE.value,  # To be resolved in evaluation step
                )
                accepted_jobs.append((job, item))
                preview_items.append(item)

        # 5. AI Evaluation of Accepted Jobs
        gemini_requests_made = 0
        total_evaluated_live = 0
        total_evaluated_cached = 0
        total_evaluations_skipped = 0
        total_evaluations_failed = 0
        total_partial_descriptions = 0

        for job, item in accepted_jobs:
            if item.is_partial_description:
                total_partial_descriptions += 1
                total_evaluations_skipped += 1
                item.evaluation_status = EvaluationStatus.SKIPPED_PARTIAL_DESCRIPTION.value
                item.evaluation_provenance = None
                item.practical_warnings = extract_practical_warnings(job)
                logger.info(
                    "Preview: Skipping AI evaluation for '%s' @ '%s' due to partial description (%d chars).",
                    job.title, job.company, item.description_char_count
                )
                continue

            if not self.config.enable_ai_evaluation:
                total_evaluations_skipped += 1
                item.evaluation_status = EvaluationStatus.SKIPPED_PARTIAL_DESCRIPTION.value
                item.practical_warnings = extract_practical_warnings(job)
                continue

            # Check cache first
            prof_hash = compute_profile_hash(profile)
            job_h = compute_job_hash(
                platform=job.platform,
                platform_job_id=job.platform_job_id,
                title=job.title,
                company=job.company,
                description=job.description_snippet or "",
            )
            cached_eval = None
            if self.resume_service is not None:
                try:
                    cached_eval = self.resume_service.get_cached_match(
                        profile_hash=prof_hash,
                        platform=job.platform,
                        platform_job_id=job.platform_job_id,
                        job_hash=job_h,
                        model_version=self.ai_service.model_name,
                        prompt_version=CURRENT_PROMPT_VERSION,
                    )
                except Exception as exc:
                    logger.debug("Cache lookup skipped: %s", exc)

            if cached_eval is not None:
                total_evaluated_cached += 1
                item.evaluation_status = EvaluationStatus.CACHED.value
                item.evaluation_provenance = MatchProvenance.AI_CACHED.value
                item.ai_score = cached_eval.match_score
                item.fit_verdict = cached_eval.fit_verdict.value if hasattr(cached_eval.fit_verdict, "value") else str(cached_eval.fit_verdict)
                item.confidence_score = cached_eval.confidence_score
                item.matching_skills = cached_eval.matching_skills
                item.missing_skills = cached_eval.missing_skills
                item.transferable_skills = cached_eval.transferable_skills
                item.reasoning = cached_eval.reasoning
                item.uncertainties = cached_eval.uncertainties
                item.practical_warnings = extract_practical_warnings(job, cached_eval if isinstance(cached_eval, AIMatchEvaluationV2) else None)
                continue

            # Live AI evaluation via AIMatcher (with skip_cache_save=True for preview mode boundary)
            try:
                eval_v2 = self.matcher.evaluate_job_ai_first(
                    job=job,
                    candidate=profile,
                    ai_service=self.ai_service,
                    resume_service=self.resume_service,
                    db_conn=None,
                    skip_cache_save=True,  # Mandatory zero cache writes in preview mode
                )

                if eval_v2.match_source == MatchProvenance.DETERMINISTIC_FALLBACK:
                    item.evaluation_status = EvaluationStatus.FALLBACK_QUOTA_EXHAUSTED.value
                    item.evaluation_provenance = MatchProvenance.DETERMINISTIC_FALLBACK.value
                    item.failure_reason = eval_v2.fallback_reason or "Daily quota exhausted"
                elif eval_v2.match_source == MatchProvenance.AI_CACHED:
                    total_evaluated_cached += 1
                    item.evaluation_status = EvaluationStatus.CACHED.value
                    item.evaluation_provenance = MatchProvenance.AI_CACHED.value
                else:
                    gemini_requests_made += 1
                    total_evaluated_live += 1
                    item.evaluation_status = EvaluationStatus.LIVE.value
                    item.evaluation_provenance = MatchProvenance.AI_LIVE.value

                item.ai_score = eval_v2.match_score
                item.fit_verdict = eval_v2.fit_verdict.value if hasattr(eval_v2.fit_verdict, "value") else str(eval_v2.fit_verdict)
                item.confidence_score = eval_v2.confidence_score
                item.matching_skills = eval_v2.matching_skills
                item.missing_skills = eval_v2.missing_skills
                item.transferable_skills = eval_v2.transferable_skills
                item.reasoning = eval_v2.reasoning
                item.uncertainties = eval_v2.uncertainties
                item.practical_warnings = extract_practical_warnings(job, eval_v2)

            except AIDailyQuotaExhaustedError as quota_err:
                item.evaluation_status = EvaluationStatus.FALLBACK_QUOTA_EXHAUSTED.value
                item.evaluation_provenance = MatchProvenance.DETERMINISTIC_FALLBACK.value
                item.failure_reason = str(quota_err)
                item.practical_warnings = extract_practical_warnings(job)

            except Exception as eval_err:
                # Fail closed on all provider, connectivity, timeout, or formatting errors
                total_evaluations_failed += 1
                item.evaluation_status = EvaluationStatus.FAILED.value
                item.evaluation_provenance = None
                item.failure_reason = str(eval_err)
                item.practical_warnings = extract_practical_warnings(job)
                logger.error("AI Evaluation failed closed for '%s' @ '%s': %s", job.title, job.company, eval_err)

        report = PreviewReport(
            sources_configured=self.config.sources,
            sources_attempted=attempted_sources,
            sources_succeeded=succeeded_sources,
            sources_failed=failed_sources,
            total_discovered_raw=total_discovered_raw,
            total_unique_accepted=len(accepted_jobs),
            total_duplicates_skipped=total_duplicates_skipped,
            total_partial_descriptions=total_partial_descriptions,
            total_evaluated_live=total_evaluated_live,
            total_evaluated_cached=total_evaluated_cached,
            total_evaluations_skipped=total_evaluations_skipped,
            total_evaluations_failed=total_evaluations_failed,
            gemini_requests_made=gemini_requests_made,
            rate_limit_policy=f"Max {self.config.max_rpm} actual requests per rolling {self.config.rate_limit_window_seconds:.0f}-second window enforced via SharedRollingRateLimiter",
            daily_quota_guardrail=f"{self.config.daily_quota_limit} calls per UTC day",
            is_preview=True,
            jobs=preview_items,
        )

        return report
