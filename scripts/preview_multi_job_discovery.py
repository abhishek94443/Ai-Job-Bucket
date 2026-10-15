import asyncio
import os
import sys
from typing import List, Dict, Any

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, WORKSPACE_ROOT)

from src.resume_service import ResumeService
from src.discovery import JobDiscoveryService
from src.deduplicator import Deduplicator
from src.ai_service import compute_profile_hash, compute_job_hash, resolve_model_name, PROMPT_VERSION_V2
from src.models import JobRecord
from src.db_config import connect_database

async def generate_preview():
    resume_svc = ResumeService()
    candidate = resume_svc.get_active_profile()
    profile_hash = compute_profile_hash(candidate)
    model_version = resolve_model_name()

    print("=" * 80)
    print("JOB BUCKET — PHASE 5B: BOUNDED DISCOVERY & CACHE PREVIEW (ZERO AI CALLS)")
    print("=" * 80)
    print(f"Candidate: {candidate.name}")
    print(f"Target Title: {candidate.target_title}")
    print(f"Total Experience: {candidate.total_experience_years} (Confirmed: {candidate.experience_confirmed})")
    print(f"Profile Hash: {profile_hash}")
    print(f"Target Model: {model_version} | Prompt Version: {PROMPT_VERSION_V2}")

    discovery = JobDiscoveryService(profile=candidate)
    deduplicator = Deduplicator()

    raw_jobs: List[JobRecord] = []

    # 1. Bounded Freehire discovery (limit 3)
    print("\n[Discovery 1/3] Querying Freehire for 'java' in Bengaluru (limit 3)...")
    try:
        fh_jobs = await discovery.search_freehire(
            query="java",
            country_code="in",
            cities="Bengaluru",
            posted_within_days=7,
            limit=3,
            max_pages=1,
            max_results=3,
        )
        print(f"  -> Freehire returned: {len(fh_jobs)} jobs")
        raw_jobs.extend(fh_jobs)
    except Exception as exc:
        print(f"  -> Freehire query error: {exc}")

    # 2. Bounded Indeed discovery if needed to reach up to 5 unique jobs
    if len(raw_jobs) < 5:
        print("\n[Discovery 2/3] Querying Indeed for 'Java Developer' in Bengaluru (limit 3)...")
        try:
            ind_jobs = await discovery.search_indeed(
                keyword="Java Developer",
                location="Bengaluru",
                country="India",
                hours_old=48,
                limit=3,
                max_pages=1,
                max_results=3,
                fetch_details=True,
            )
            print(f"  -> Indeed returned: {len(ind_jobs)} jobs")
            raw_jobs.extend(ind_jobs)
        except Exception as exc:
            print(f"  -> Indeed query error: {exc}")

    # 3. Deduplicate across sources
    unique_jobs = deduplicator.deduplicate(raw_jobs)[:5]
    print(f"\n[Deduplication] Raw jobs: {len(raw_jobs)} -> Unique jobs (capped at 5): {len(unique_jobs)}")

    # 4. Check cache status for each unique job
    print("\n[Cache Hit/Miss Audit]")
    cache_hits = 0
    cache_misses = 0
    job_details = []

    for idx, job in enumerate(unique_jobs, 1):
        job_h = compute_job_hash(
            platform=job.platform,
            platform_job_id=job.platform_job_id,
            title=job.title,
            company=job.company,
            description=job.description_snippet or "",
        )
        cached_eval = resume_svc.get_cached_match(
            profile_hash=profile_hash,
            platform=job.platform,
            platform_job_id=job.platform_job_id,
            job_hash=job_h,
            model_version=model_version,
            prompt_version=PROMPT_VERSION_V2,
        )
        is_hit = cached_eval is not None
        if is_hit:
            cache_hits += 1
            status_str = f"HIT (Score: {cached_eval.match_score}, Verdict: {cached_eval.fit_verdict.value if hasattr(cached_eval.fit_verdict, 'value') else cached_eval.fit_verdict})"
        else:
            cache_misses += 1
            status_str = "MISS"

        job_info = {
            "index": idx,
            "platform": job.platform,
            "platform_job_id": job.platform_job_id,
            "title": job.title,
            "company": job.company,
            "location": job.location or "N/A",
            "job_hash": job_h,
            "cache_status": status_str,
            "is_hit": is_hit,
        }
        job_details.append(job_info)
        print(f"  [{idx}] [{job.platform.upper()}] {job.title} @ {job.company}")
        print(f"      Platform Job ID: {job.platform_job_id}")
        print(f"      Content Hash:    {job_h[:16]}...")
        print(f"      Location:        {job.location or 'N/A'}")
        print(f"      Cache Status:    {status_str}")

    print("\n" + "=" * 80)
    print("SUMMARY PREVIEW:")
    print(f"  Total Unique Jobs Selected: {len(unique_jobs)} (Maximum limit: 5)")
    print(f"  Expected Cache Hits:        {cache_hits} (Will make 0 Gemini requests, consume 0 quota)")
    print(f"  Expected Cache Misses:      {cache_misses} (Would require live evaluation if authorized)")
    print(f"  Expected Live LLM Calls:    {cache_misses}")
    print(f"  Zero Gemini calls were dispatched during this preview.")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(generate_preview())
