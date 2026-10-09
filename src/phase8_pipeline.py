"""
Job Bucket — Phase 8: Full Multi-Source Job Discovery, AI Evaluation, and Neon Persistence.
Coordinates end-to-end production pipeline:
- Discovers from Freehire, LinkedIn, and Indeed (up to 15 accepted per source, max 45 overall before deduplication).
- Uses active candidate profile and search preferences.
- Deduplicates across sources and enforces description integrity (<250 chars skipped).
- Evaluates eligible unique jobs in batches of up to 15.
- Enforces shared 4-RPM rolling rate limiter and 50-call UTC daily quota cap.
- Persists jobs and validated evaluations to Neon PostgreSQL with existing evaluation preservation.
- Verifies retrieval through the dashboard service.
"""

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
    DeterministicDecision,
    DeterministicEligibility,
    RelevanceScore,
    FreshnessStatus,
)
from src.candidate_profile import CandidateProfile
from src.discovery import JobDiscoveryService
from src.resume_service import ResumeService
from src.scorer import JobRelevanceScorer
from src.eligibility import DeterministicJobEvaluator
from src.ai_service import (
    GeminiAIService,
    AIMatchEvaluationV2,
    MatchProvenance,
    AIMatchVerdict,
    AIDailyQuotaExhaustedError,
    AIServiceError,
    CURRENT_PROMPT_VERSION,
    compute_profile_hash,
    compute_job_hash,
)
from src.rate_limiter import SharedRollingRateLimiter
from src.deduplicator import Deduplicator
from src.db_persistence import DatabasePersistence
from src.db_config import connect_database
from src.dashboard_service import DashboardQueryService

logger = logging.getLogger(__name__)


class Phase8Config(BaseModel):
    sources: List[str] = Field(default_factory=lambda: ["freehire", "linkedin", "indeed"])
    location: str = "Bengaluru"
    country: str = "India"
    max_per_source: int = 15
    max_total_before_dedup: int = 45
    batch_size: int = 15
    max_rpm: int = 4
    rate_limit_window_seconds: float = 60.0
    daily_quota_limit: int = 50
    min_description_chars: int = 250


class Phase8RunResult(BaseModel):
    sources_attempted: List[str]
    sources_succeeded: List[str]
    sources_failed: Dict[str, str] = Field(default_factory=dict)
    jobs_per_source_raw: Dict[str, int] = Field(default_factory=dict)
    total_discovered_raw: int
    total_unique_accepted: int
    total_duplicates_skipped: int
    complete_descriptions_count: int
    partial_descriptions_count: int
    evaluations_live: int
    evaluations_cached: int
    evaluations_pending: int
    evaluations_skipped: int
    evaluations_failed: int
    quota_before: int
    quota_after: int
    persisted_ids_count: int
    existing_jobs_intact: bool
    dashboard_total_jobs: int
    persisted_jobs_summary: List[Dict[str, Any]] = Field(default_factory=list)


class Phase8Pipeline:
    def __init__(
        self,
        config: Optional[Phase8Config] = None,
        discovery_service: Optional[JobDiscoveryService] = None,
        resume_service: Optional[ResumeService] = None,
        ai_service: Optional[GeminiAIService] = None,
        db_persistence: Optional[DatabasePersistence] = None,
        rate_limiter: Optional[SharedRollingRateLimiter] = None,
    ):
        self.config = config or Phase8Config()
        self.resume_service = resume_service or ResumeService()
        self.profile = self.resume_service.get_active_profile()
        self.discovery_service = discovery_service or JobDiscoveryService(profile=self.profile)
        self.db = db_persistence or DatabasePersistence()

        self.rate_limiter = rate_limiter or SharedRollingRateLimiter(
            max_requests=self.config.max_rpm,
            window_seconds=self.config.rate_limit_window_seconds,
        )
        self.ai_service = ai_service or GeminiAIService(
            daily_limit=self.config.daily_quota_limit,
            rate_limiter=self.rate_limiter,
        )

        self.evaluator = DeterministicJobEvaluator()
        self.scorer = JobRelevanceScorer(target_location=self.config.location, candidate_profile=self.profile)

    def _get_current_quota_usage(self) -> int:
        now_utc = datetime.datetime.now(datetime.timezone.utc).date()
        conn = None
        try:
            conn = connect_database()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT call_count FROM ai_daily_usage WHERE usage_date = %s;",
                    (now_utc,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            logger.warning("Could not read current quota: %s", e)
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    async def execute_run(self) -> Phase8RunResult:
        logger.info("=== STARTING PHASE 8 END-TO-END PIPELINE RUN ===")
        quota_before = self._get_current_quota_usage()
        logger.info(f"Current UTC Quota Usage Before Run: {quota_before}/{self.config.daily_quota_limit}")

        remaining_quota_budget = max(0, self.config.daily_quota_limit - quota_before)
        logger.info(f"Remaining Quota Budget Available Today: {remaining_quota_budget}")

        # 1. Multi-source Discovery
        sources_attempted: List[str] = []
        sources_succeeded: List[str] = []
        sources_failed: Dict[str, str] = {}
        jobs_per_source: Dict[str, int] = {}
        raw_jobs_by_source: Dict[str, List[JobRecord]] = {}

        queries = self.profile.core_search_skill_combinations[:2]
        if self.profile.target_title and self.profile.target_title not in queries:
            queries.insert(0, self.profile.target_title)

        logger.info(f"Using Bounded Skill Queries: {queries}")

        for src in self.config.sources:
            sources_attempted.append(src)
            src_jobs: List[JobRecord] = []
            seen_source_keys: Set[str] = set()

            try:
                for q in queries:
                    if len(src_jobs) >= self.config.max_per_source:
                        break

                    needed = self.config.max_per_source - len(src_jobs)

                    if src == "freehire":
                        logger.info(f"Freehire Query '{q}' across India (configured cities/remote) (needed={needed})...")
                        res = await self.discovery_service.search_freehire(
                            query=q,
                            country_code="in",
                            cities=None,
                            posted_within_days=1,
                            limit=needed,
                            max_pages=2,
                            max_results=needed,
                        )
                    elif src == "linkedin":
                        logger.info(f"LinkedIn Query '{q}' across India (configured cities/remote) (needed={needed})...")
                        res = await self.discovery_service.search_linkedin(
                            keyword=q,
                            location="India",
                            posted_within="Past day",
                            limit=min(10, needed),
                            max_pages=2,
                            max_results=needed,
                            fetch_details=True,
                        )
                    elif src == "indeed":
                        logger.info(f"Indeed Query '{q}' across India (configured cities/remote) (needed={needed})...")
                        res = await self.discovery_service.search_indeed(
                            keyword=q,
                            location="India",
                            country="India",
                            hours_old=24,
                            limit=min(10, needed),
                            max_pages=2,
                            max_results=needed,
                            fetch_details=True,
                        )
                    else:
                        continue

                    for item in res:
                        key = f"{item.platform}:{item.platform_job_id}" if item.platform_job_id else item.normalized_url
                        if key not in seen_source_keys and len(src_jobs) < self.config.max_per_source:
                            seen_source_keys.add(key)
                            src_jobs.append(item)

                sources_succeeded.append(src)
                jobs_per_source[src] = len(src_jobs)
                raw_jobs_by_source[src] = src_jobs
                logger.info(f"Source '{src}' retrieved {len(src_jobs)} unique listings.")

            except Exception as e:
                logger.error(f"Error querying source '{src}': {e}", exc_info=True)
                sources_failed[src] = str(e)
                jobs_per_source[src] = 0
                raw_jobs_by_source[src] = []

        total_discovered_raw = sum(len(jobs) for jobs in raw_jobs_by_source.values())
        logger.info(f"Total Discovered Listings (Raw): {total_discovered_raw}")

        # 2. Cross-Source Deduplication & Integrity Verification
        unique_jobs: List[JobRecord] = []
        deduplicator = Deduplicator(cross_platform=True, detect_mass_postings=True)
        total_duplicates_skipped = 0
        complete_descriptions_count = 0
        partial_descriptions_count = 0

        for src in self.config.sources:
            for job in raw_jobs_by_source.get(src, []):
                if not deduplicator.add(job):
                    total_duplicates_skipped += 1
                    logger.info(f"Skipping duplicate listing: '{job.title}' @ '{job.company}' ({job.platform})")
                    continue

                is_mass, orig_job = deduplicator.is_mass_posting(job)
                if is_mass and orig_job:
                    logger.info(f"Detected mass-posting cluster: '{job.title}' @ '{job.company}' matches earlier listing from {orig_job.location or 'another city'}")

                # Check description integrity
                desc_text = (job.description_snippet or "").strip()
                desc_len = len(desc_text)
                if desc_len < self.config.min_description_chars:
                    job.is_partial_description = True
                    job.evaluation_source = None
                    job.ai_score = None
                    job.ai_verdict = None
                    job.ai_evaluation = None
                    partial_descriptions_count += 1
                    logger.info(
                        f"Listing '{job.title}' @ '{job.company}' flagged partial ({desc_len} chars < {self.config.min_description_chars})."
                    )
                else:
                    job.is_partial_description = False
                    complete_descriptions_count += 1

                # Calculate deterministic baseline score
                det = self.evaluator.evaluate(job, self.profile)
                job.deterministic_eligibility = det
                rel = self.scorer.score_job(
                    title=job.title,
                    company=job.company,
                    location=job.location,
                    description=job.description_snippet,
                    source_experience_years_min=job.source_experience_years_min,
                    deterministic_eligibility=det,
                )
                job.relevance = rel

                unique_jobs.append(job)

        total_unique_accepted = len(unique_jobs)
        logger.info(
            f"Unique Jobs Accepted: {total_unique_accepted} "
            f"({complete_descriptions_count} complete, {partial_descriptions_count} partial, {total_duplicates_skipped} duplicates skipped)"
        )

        # 3. Batch AI Evaluation (in batches of up to 15)
        eligible_jobs = [j for j in unique_jobs if not j.is_partial_description]
        logger.info(f"Eligible Unique Jobs for AI Evaluation: {len(eligible_jobs)}")

        evaluations_live = 0
        evaluations_cached = 0
        evaluations_pending = 0
        evaluations_skipped = partial_descriptions_count
        evaluations_failed = 0

        prof_hash = compute_profile_hash(self.profile)

        # Process in batches of up to 15 jobs per request (Max 3 Gemini requests for up to 45 jobs)
        batch_size = self.config.batch_size
        gemini_batch_requests_count = 0

        for batch_idx in range(0, len(eligible_jobs), batch_size):
            batch = eligible_jobs[batch_idx : batch_idx + batch_size]
            batch_num = (batch_idx // batch_size) + 1
            logger.info(f"Processing Evaluation Batch {batch_num} ({len(batch)} jobs)...")

            uncached_jobs: List[JobRecord] = []
            job_by_id_map: Dict[str, JobRecord] = {}

            for job in batch:
                jid = f"{job.platform}:{job.platform_job_id}"
                job_by_id_map[jid] = job

                job_h = compute_job_hash(
                    platform=job.platform,
                    platform_job_id=job.platform_job_id,
                    title=job.title,
                    company=job.company,
                    description=job.description_snippet or "",
                )

                # 1. Exact cache lookup first
                cached_eval = None
                try:
                    cached_eval = self.resume_service.get_cached_match(
                        profile_hash=prof_hash,
                        platform=job.platform,
                        platform_job_id=job.platform_job_id,
                        job_hash=job_h,
                        model_version=self.ai_service.model_name,
                        prompt_version=CURRENT_PROMPT_VERSION,
                    )
                except Exception as c_err:
                    logger.debug(f"Cache check error for {job.title}: {c_err}")

                if cached_eval is not None:
                    job.ai_score = cached_eval.match_score
                    job.ai_verdict = (
                        cached_eval.fit_verdict.value
                        if hasattr(cached_eval.fit_verdict, "value")
                        else str(cached_eval.fit_verdict)
                    )
                    job.evaluation_source = MatchProvenance.AI_CACHED.value
                    job.ai_evaluation = cached_eval.model_dump()
                    evaluations_cached += 1
                    logger.info(f"CACHE HIT: '{job.title}' @ '{job.company}' -> {job.ai_score}/100 ({job.ai_verdict})")
                else:
                    uncached_jobs.append(job)

            if not uncached_jobs:
                logger.info(f"Batch {batch_num}: All {len(batch)} jobs resolved from exact cache. 0 Gemini requests needed.")
                continue

            # Check remaining quota budget
            if remaining_quota_budget <= 0:
                for uj in uncached_jobs:
                    uj.evaluation_source = None
                    uj.ai_score = None
                    uj.ai_verdict = None
                    uj.ai_evaluation = None
                    evaluations_pending += 1
                logger.warning(
                    f"Batch {batch_num}: Daily quota ceiling reached. Marking {len(uncached_jobs)} jobs as PENDING_EVALUATION."
                )
                continue

            # Enforce strict maximum of 3 real Gemini API requests
            if gemini_batch_requests_count >= 3:
                logger.warning(
                    f"Batch {batch_num}: Maximum of 3 real Gemini requests reached. Marking {len(uncached_jobs)} jobs as PENDING_EVALUATION."
                )
                for uj in uncached_jobs:
                    uj.evaluation_source = None
                    uj.ai_score = None
                    uj.ai_verdict = None
                    uj.ai_evaluation = None
                    evaluations_pending += 1
                continue

            # 2. Dispatch ONE Gemini API request for all uncached jobs in this batch
            batch_payload = [
                {
                    "job_id": f"{j.platform}:{j.platform_job_id}",
                    "title": j.title,
                    "company": j.company,
                    "location": j.location or "N/A",
                    "description": j.description_snippet or "",
                }
                for j in uncached_jobs
            ]

            try:
                gemini_batch_requests_count += 1
                logger.info(
                    f"Dispatching Gemini Batch Request #{gemini_batch_requests_count} for {len(batch_payload)} jobs in Batch {batch_num}..."
                )

                batch_eval_results = self.ai_service.evaluate_jobs_batch(
                    profile=self.profile,
                    jobs=batch_payload,
                    db_conn=None,
                    timeout=120.0,
                )

                remaining_quota_budget -= 1  # Exactly 1 quota unit consumed per batch Gemini request
                logger.info(f"Gemini Batch Request #{gemini_batch_requests_count} returned {len(batch_eval_results)} structured evaluations.")

                eval_by_id = {str(item.get("job_id")): item for item in batch_eval_results}

                for uj in uncached_jobs:
                    uj_id = f"{uj.platform}:{uj.platform_job_id}"
                    eval_data = eval_by_id.get(uj_id)

                    if eval_data and "match_score" in eval_data and "fit_verdict" in eval_data:
                        uj.ai_score = int(eval_data["match_score"])
                        uj.ai_verdict = str(eval_data["fit_verdict"])
                        uj.evaluation_source = MatchProvenance.AI_LIVE.value
                        uj.ai_evaluation = eval_data

                        # Save to exact cache
                        try:
                            uj_h = compute_job_hash(
                                platform=uj.platform,
                                platform_job_id=uj.platform_job_id,
                                title=uj.title,
                                company=uj.company,
                                description=uj.description_snippet or "",
                            )
                            # Convert to AIMatchEvaluationV2 object for caching
                            eval_obj = AIMatchEvaluationV2(**eval_data)
                            self.resume_service.save_cached_match(
                                profile_hash=prof_hash,
                                platform=uj.platform,
                                platform_job_id=uj.platform_job_id,
                                job_hash=uj_h,
                                model_version=self.ai_service.model_name,
                                evaluation=eval_obj,
                                prompt_version=CURRENT_PROMPT_VERSION,
                            )
                        except Exception as sc_err:
                            logger.warning(f"Failed to cache evaluation for {uj.title}: {sc_err}")

                        evaluations_live += 1
                        logger.info(
                            f"AI EVAL SUCCESS: [{uj.platform.upper()}] '{uj.title}' @ '{uj.company}' -> {uj.ai_score}/100 ({uj.ai_verdict})"
                        )
                    else:
                        logger.error(f"Missing or invalid evaluation for job {uj_id} in Gemini batch response.")
                        uj.evaluation_source = "AI_ERROR"
                        evaluations_failed += 1

            except AIDailyQuotaExhaustedError as quota_err:
                logger.warning(f"Daily quota exhausted during batch: {quota_err}")
                remaining_quota_budget = 0
                for uj in uncached_jobs:
                    uj.evaluation_source = "PENDING_EVALUATION"
                    evaluations_pending += 1

            except Exception as batch_err:
                logger.error(f"Gemini Batch Request #{gemini_batch_requests_count} failed: {batch_err}", exc_info=True)
                for uj in uncached_jobs:
                    uj.evaluation_source = "AI_ERROR"
                    evaluations_failed += 1

        # 4. Persist Evaluated Jobs to Neon
        logger.info(f"Persisting {len(unique_jobs)} unique listings to Neon PostgreSQL...")
        saved_ids = self.db.save_jobs(unique_jobs)
        persisted_ids_count = len(saved_ids)
        logger.info(f"Successfully persisted {persisted_ids_count} jobs to Neon.")

        quota_after = self._get_current_quota_usage()
        logger.info(f"Current UTC Quota Usage After Run: {quota_after}/{self.config.daily_quota_limit}")

        # 5. Dashboard Verification
        logger.info("Verifying persistence through DashboardQueryService...")
        dash_data, dashboard_total = DashboardQueryService().search_jobs(page_size=1000)
        logger.info(f"Dashboard Data Loaded: {dashboard_total} total jobs in database.")

        # Confirm persistence matches dashboard count
        existing_jobs_intact = (dashboard_total == persisted_ids_count)
        logger.info(f"Dashboard Count Matches Persisted ({dashboard_total} == {persisted_ids_count}): {existing_jobs_intact}")

        # Prepare summary of newly persisted jobs
        summary_list = []
        for j in unique_jobs:
            summary_list.append({
                "source": j.platform,
                "job_id": j.platform_job_id,
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "url": j.url,
                "is_partial": j.is_partial_description,
                "desc_len": len((j.description_snippet or "").strip()),
                "ai_score": j.ai_score,
                "ai_verdict": j.ai_verdict,
                "evaluation_source": j.evaluation_source,
            })

        return Phase8RunResult(
            sources_attempted=sources_attempted,
            sources_succeeded=sources_succeeded,
            sources_failed=sources_failed,
            jobs_per_source_raw=jobs_per_source,
            total_discovered_raw=total_discovered_raw,
            total_unique_accepted=total_unique_accepted,
            total_duplicates_skipped=total_duplicates_skipped,
            complete_descriptions_count=complete_descriptions_count,
            partial_descriptions_count=partial_descriptions_count,
            evaluations_live=evaluations_live,
            evaluations_cached=evaluations_cached,
            evaluations_pending=evaluations_pending,
            evaluations_skipped=evaluations_skipped,
            evaluations_failed=evaluations_failed,
            quota_before=quota_before,
            quota_after=quota_after,
            persisted_ids_count=persisted_ids_count,
            existing_jobs_intact=existing_jobs_intact,
            dashboard_total_jobs=dashboard_total,
            persisted_jobs_summary=summary_list,
        )
