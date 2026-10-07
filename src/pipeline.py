import asyncio
import logging
import argparse
import hashlib
from typing import List, Optional

from src.candidate_profile import DEFAULT_CANDIDATE_PROFILE, CandidateProfile
from src.discovery import JobDiscoveryService
from src.deduplicator import Deduplicator, compute_canonical_job_key
from src.browser_validator import BrowserListingValidator
from src.scorer import JobRelevanceScorer, ScoringWeights
from src.exporter import export_jobs_to_json
from src.models import JobRecord, FitEligibility, ExperienceEligibility, FreshnessStatus, EnrichmentStatus
from src.eligibility import DeterministicJobEvaluator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def run_pipeline(
    profile: Optional[CandidateProfile] = None,
    skill_queries: Optional[List[str]] = None,
    location: str = "Bengaluru",
    country: str = "India",
    limit_per_source: Optional[int] = None,
    min_score_report: int = 60,
    persist_to_db: bool = False,
    ai_dry_run: bool = False,
) -> List[JobRecord]:
    if profile is None:
        try:
            from src.resume_service import ResumeService
            candidate = ResumeService().get_active_profile()
        except Exception:
            candidate = DEFAULT_CANDIDATE_PROFILE
    else:
        candidate = profile
    queries = skill_queries or candidate.core_search_skill_combinations

    logger.info(f"Starting Resume-Skill-First Job Discovery Pipeline for {candidate.name} ({candidate.target_experience_years} YOE)...")
    logger.info(f"Queries ({len(queries)}): {queries} | Target Hub: {location}, {country} | Limit/source: {limit_per_source or 'Uncapped'}")

    discovery = JobDiscoveryService(profile=candidate)
    deduplicator = Deduplicator()
    browser_validator = BrowserListingValidator()
    evaluator = DeterministicJobEvaluator()
    scorer = JobRelevanceScorer(target_location=location, candidate_profile=candidate)

    raw_jobs: List[JobRecord] = []

    # 1. Discover Freehire jobs (Targeting past 24 hours: posted_within_days=1)
    logger.info("--- 1. Searching Freehire (Skill-First, Posted Within 24h) ---")
    for q in queries:
        try:
            fh_jobs = await discovery.search_freehire(
                query=q,
                country_code="in",
                cities=location,
                posted_within_days=1,
                limit=limit_per_source or 50,
                max_results=limit_per_source,
            )
            logger.info(f"Freehire '{q}' retrieved: {len(fh_jobs)} jobs.")
            raw_jobs.extend(fh_jobs)
        except Exception as e:
            logger.warning(f"Error querying Freehire for '{q}': {e}")

    # 2. Discover LinkedIn jobs (Targeting past day: posted_within='Past day')
    logger.info("--- 2. Searching LinkedIn (Skill-First, Past Day) ---")
    for q in queries:
        try:
            li_jobs = await discovery.search_linkedin(
                keyword=q,
                location=f"{location}, Karnataka, {country}",
                posted_within="Past day",
                limit=limit_per_source or 25,
                max_results=limit_per_source,
                fetch_details=False,
            )
            logger.info(f"LinkedIn '{q}' retrieved: {len(li_jobs)} jobs.")
            raw_jobs.extend(li_jobs)
        except Exception as e:
            logger.warning(f"Error querying LinkedIn for '{q}': {e}")

    # 3. Discover Indeed jobs (Targeting past 24 hours: hours_old=24)
    logger.info("--- 3. Searching Indeed (Skill-First, Past 24h) ---")
    for q in queries:
        try:
            ind_jobs = await discovery.search_indeed(
                keyword=q,
                location=location,
                country=country,
                hours_old=24,
                limit=limit_per_source or 25,
                max_results=limit_per_source,
                fetch_details=True,
            )
            logger.info(f"Indeed '{q}' retrieved: {len(ind_jobs)} jobs.")
            raw_jobs.extend(ind_jobs)
        except Exception as e:
            logger.warning(f"Error querying Indeed for '{q}': {e}")

    logger.info(f"Total raw jobs retrieved across all sources: {len(raw_jobs)}")

    # 4. Deduplicate across sources
    unique_jobs = deduplicator.deduplicate(raw_jobs)
    logger.info(f"Total unique jobs after cross-source deduplication: {len(unique_jobs)}")

    # 5. Validation and Deterministic Eligibility & Relevance Scoring
    final_jobs: List[JobRecord] = []
    for job in unique_jobs:
        # Compute canonical key and collision-resistant hash
        job.canonical_key = compute_canonical_job_key(job.company, job.title, job.location)
        job.canonical_hash = hashlib.md5(job.canonical_key.encode("utf-8")).hexdigest()

        # Track character budget and token estimate for quota-aware batching
        desc = job.description_snippet or ""
        job.char_count = len(desc)
        job.estimated_tokens = max(1, (job.char_count + 3) // 4)
        job.enrichment_status = EnrichmentStatus.STAGED_DETERMINISTIC.value

        validated_evidence = await browser_validator.probe_listing(job)
        job.validation = validated_evidence

        # Deterministic eligibility evaluation against candidate constraints
        det_eligibility = evaluator.evaluate(job, candidate)
        job.deterministic_eligibility = det_eligibility

        # Relevance scoring incorporating resume skills, experience eligibility, multi-city tech hubs
        relevance = scorer.score_job(
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description_snippet,
            source_experience_years_min=job.source_experience_years_min,
            deterministic_eligibility=det_eligibility,
        )
        job.relevance = relevance
        final_jobs.append(job)

    # 6. Export all discovered jobs to timestamped JSON
    output_file = export_jobs_to_json(final_jobs, output_dir="data")
    logger.info(f"Successfully exported {len(final_jobs)} jobs to {output_file}")

    # 7. Optional PostgreSQL persistence
    if persist_to_db:
        from src.db_persistence import DatabasePersistence
        db = DatabasePersistence()
        healthy, msg = db.check_health()
        if healthy:
            logger.info("Persisting jobs to PostgreSQL...")
            saved_ids = db.save_jobs(final_jobs)
            logger.info(f"Successfully persisted/updated {len(saved_ids)} jobs in database.")
        else:
            logger.warning(f"Skipping database persistence (PostgreSQL not reachable: {msg})")

    # 8. Summary Discovery Report
    high_fit_jobs = [j for j in final_jobs if j.relevance and j.relevance.eligibility == FitEligibility.HIGH_FIT]
    prov_jobs = [j for j in final_jobs if j.relevance and j.relevance.eligibility == FitEligibility.PROVISIONAL_NEEDS_REVIEW]
    confirmed_fresh = [j for j in final_jobs if j.freshness_status == FreshnessStatus.CONFIRMED_FRESH]

    print("\n" + "=" * 95)
    print(f"=== RESUME-SKILL-FIRST JOB DISCOVERY REPORT (Threshold: {min_score_report}/100) ===")
    print(f"Total Unique: {len(final_jobs)} | High-Fit (Confirmed): {len(high_fit_jobs)} | Provisional: {len(prov_jobs)} | Confirmed Fresh (24h): {len(confirmed_fresh)}")
    print("=" * 95)
    for j in sorted(final_jobs, key=lambda x: (x.relevance.total_score if x.relevance else 0), reverse=True):
        score = j.relevance.total_score if j.relevance else 0
        level = j.relevance.experience_level.value if j.relevance else "UNKNOWN"
        exp_elig = j.relevance.experience_eligibility.value if j.relevance else "UNKNOWN"
        conf = j.relevance.confidence_level.value if j.relevance else "LOW"
        elig = j.relevance.eligibility.value if j.relevance else "LOW_FIT"
        fresh_stat = j.freshness_status.value
        status = j.validation.job_status.value
        app_type = j.validation.application_type.value

        print(f"[{elig:<24} | {conf} CONF] Score: {score:>2}/100 | Exp: {level} ({exp_elig}) | Freshness: {fresh_stat}")
        print(f"  [{j.platform.upper()}] {j.title} @ {j.company} | Location: {j.location or 'N/A'}")
        print(f"  Listing Status: {status} | App Type: {app_type} | Posted: {j.posted_date or 'N/A'}")
        if j.relevance and j.relevance.matched_skills:
            print(f"  Confirmed Skills: {', '.join(j.relevance.matched_skills)}")
        else:
            print(f"  Confirmed Skills: None (unobserved in metadata)")
        if j.relevance and j.relevance.eligibility_reason:
            print(f"  Rationale: {j.relevance.eligibility_reason}")
        print("-" * 95)

    # 9. AI Semantic Matching Dry Run (Auditing and preview with zero external calls/writes)
    if ai_dry_run:
        from src.ai_matcher import AIMatcher
        matcher = AIMatcher()
        dry_run_report = matcher.run_dry_run(final_jobs, candidate)
        print("\n" + dry_run_report.format_summary())

    return final_jobs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job discovery pipeline")
    parser.add_argument("--location", default="Bengaluru", help="Location")
    parser.add_argument("--limit", type=int, default=3, help="Max results per search query")
    parser.add_argument("--min-score", type=int, default=60, help="Minimum relevance score for highlighting")
    parser.add_argument("--persist-db", action="store_true", help="Persist discovered jobs to PostgreSQL")
    parser.add_argument("--ai-dry-run", action="store_true", help="Run AI semantic matching dry-run audit without calling Gemini")
    args = parser.parse_args()

    asyncio.run(run_pipeline(
        location=args.location,
        limit_per_source=args.limit,
        min_score_report=args.min_score,
        persist_to_db=args.persist_db,
        ai_dry_run=args.ai_dry_run,
    ))

