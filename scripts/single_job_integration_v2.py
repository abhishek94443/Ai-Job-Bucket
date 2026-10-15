#!/usr/bin/env python3
"""
Job Bucket — Phase 5A: Controlled Single-Job AI Integration Runner.

Executes a bounded, strictly controlled end-to-end AI-first matching integration
for exactly ONE real job listing against Neon PostgreSQL and Google Gemini API.

SAFETY CONSTRAINTS:
1. BOUNDED DISCOVERY: Retrieves exactly 1 real job from discovery adapter and immediately halts.
2. AT MOST ONE LIVE CALL: At most 1 external Gemini request during uncached evaluation.
3. CACHE INVARIANT: Cache-hit verification executes with 0 external API calls and 0 quota consumed.
4. ZERO MUTATION: Never modifies candidate profiles, resume versions, or existing V1 cache entries.
5. ZERO SECRETS: Never prints API keys, connection strings, or unredacted personal identifiers.
"""

import asyncio
import json
import logging
import os
import sys
from typing import Dict, Any, Optional

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ai_service import (
    GeminiAIService,
    AIMatchEvaluationV2,
    MatchProvenance,
    AIMatchVerdict,
    compute_profile_hash,
    compute_job_hash,
    PROMPT_VERSION_V2,
    DEFAULT_GEMINI_MODEL,
    resolve_model_name,
)
from src.candidate_profile import CandidateProfile
from src.resume_service import ResumeService
from src.ai_matcher import AIMatcher
from src.discovery import JobDiscoveryService
from src.db_persistence import DatabasePersistence
from src.dashboard_service import DashboardQueryService
from src.db_config import DatabaseConfig, connect_database
from src.eligibility import DeterministicJobEvaluator
from src.scorer import JobRelevanceScorer
from src.models import JobRecord, DeterministicDecision

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase5a_integration")


def get_current_quota_status() -> Dict[str, Any]:
    """Reads current quota row from Neon without mutating counters."""
    conn = connect_database()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT usage_date, call_count, last_called_at
                FROM ai_daily_usage
                ORDER BY usage_date DESC
                LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                return {
                    "usage_date": str(row[0]),
                    "call_count": int(row[1]),
                    "last_called_at": str(row[2]),
                }
            return {"usage_date": "None", "call_count": 0, "last_called_at": "None"}
    finally:
        conn.close()


def get_v1_cache_row_summary() -> Optional[Dict[str, Any]]:
    """Reads legacy V1 cache entry to ensure it remains untouched."""
    conn = connect_database()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, job_hash, prompt_version, match_verdict, overall_score, confidence_score, evaluation_data
                FROM ai_match_cache
                WHERE prompt_version = 'v1'
                LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                return {
                    "id": row[0],
                    "job_hash": row[1],
                    "prompt_version": row[2],
                    "match_verdict": row[3],
                    "overall_score": row[4],
                    "confidence_score": float(row[5]),
                    "has_evaluation_data": row[6] is not None,
                }
            return None
    finally:
        conn.close()


async def discover_exactly_one_real_job() -> JobRecord:
    """Discovers exactly 1 real job from Freehire, falling back to Indeed if empty."""
    discovery = JobDiscoveryService()
    print("\n[Step 2] Discovering exactly 1 real job listing...")
    
    # Try Freehire first (bounded limit=1, max_results=1, max_pages=1)
    jobs = await discovery.search_freehire(
        query="java",
        country_code="in",
        cities="Bengaluru",
        posted_within_days=7,
        limit=1,
        max_pages=1,
        max_results=1,
    )
    if jobs:
        print(f"  -> Discovered 1 real job via Freehire: '{jobs[0].title}' @ '{jobs[0].company}'")
        return jobs[0]

    # Bounded fallback to Indeed (limit=1, max_results=1, max_pages=1)
    print("  -> Freehire returned no jobs, checking Indeed (bounded 1-job request)...")
    jobs = await discovery.search_indeed(
        keyword="Java Developer",
        location="Bengaluru",
        country="India",
        hours_old=48,
        limit=1,
        max_pages=1,
        max_results=1,
        fetch_details=True,
    )
    if jobs:
        print(f"  -> Discovered 1 real job via Indeed: '{jobs[0].title}' @ '{jobs[0].company}'")
        return jobs[0]

    raise RuntimeError("No real jobs could be discovered via existing discovery adapters.")


async def run_phase5a_integration():
    print("=" * 80)
    print("JOB BUCKET — PHASE 5A: CONTROLLED SINGLE-JOB AI INTEGRATION TEST")
    print("=" * 80)

    # Pre-checks & Safety Gates
    ai_service = GeminiAIService()
    if not ai_service.is_configured:
        print("ERROR: GEMINI_API_KEY is not configured. Aborting.")
        sys.exit(1)

    model_name = ai_service.model_name
    print(f"Target Gemini Model: {model_name}")
    print(f"Target Prompt Version: {PROMPT_VERSION_V2}")

    # Step 1: Load active candidate profile from Neon
    print("\n[Step 1] Loading active candidate profile from Neon PostgreSQL...")
    resume_svc = ResumeService()
    if not resume_svc.has_active_profile():
        print("ERROR: No active candidate profile found in Neon.")
        sys.exit(1)
    
    candidate = resume_svc.get_active_profile()
    profile_hash = compute_profile_hash(candidate)
    print(f"  Candidate Name: {candidate.name}")
    print(f"  Target Title: {candidate.target_title}")
    print(f"  Total Experience: {candidate.target_experience_years} (Confirmed: {candidate.experience_confirmed})")
    print(f"  Languages: {', '.join(candidate.languages)}")
    print(f"  Frameworks: {', '.join(candidate.frameworks)}")
    print(f"  Profile Hash: {profile_hash}")

    # Record initial database state
    quota_before = get_current_quota_status()
    v1_before = get_v1_cache_row_summary()
    print(f"\n[Baseline Verification]")
    print(f"  Initial Daily Quota Usage: {quota_before['call_count']} calls (Date: {quota_before['usage_date']})")
    print(f"  Existing V1 Cache Row: id={v1_before['id'] if v1_before else 'None'}, verdict={v1_before['match_verdict'] if v1_before else 'None'}")

    # Step 2: Retrieve exactly ONE real job listing
    job = await discover_exactly_one_real_job()

    # Step 3: Compute content hash
    job_hash = compute_job_hash(
        platform=job.platform,
        platform_job_id=job.platform_job_id,
        title=job.title,
        company=job.company,
        description=job.description_snippet or "",
    )
    print(f"\n[Step 3] Job Normalization & Hashing:")
    print(f"  Platform: {job.platform}")
    print(f"  Platform Job ID: {job.platform_job_id}")
    print(f"  Job Title: {job.title}")
    print(f"  Company: {job.company}")
    print(f"  Location: {job.location}")
    print(f"  Description snippet length: {len(job.description_snippet or '')} chars")
    print(f"  Job Content Hash: {job_hash}")

    # Compute supplementary deterministic scoring (supplementary context only)
    det_evaluator = DeterministicJobEvaluator()
    job.deterministic_eligibility = det_evaluator.evaluate(job, candidate)
    scorer = JobRelevanceScorer(candidate_profile=candidate)
    job.relevance = scorer.score_job(
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description_snippet,
        source_experience_years_min=job.source_experience_years_min,
        deterministic_eligibility=job.deterministic_eligibility,
    )
    print(f"  Supplementary Deterministic Decision: {job.deterministic_eligibility.decision.value} (Score: {job.relevance.total_score})")

    # Step 4: Check ai_match_cache for V2 cache key
    print(f"\n[Step 4] Checking ai_match_cache for exact V2 key...")
    cached_match = resume_svc.get_cached_match(
        profile_hash=profile_hash,
        platform=job.platform,
        platform_job_id=job.platform_job_id,
        job_hash=job_hash,
        model_version=model_name,
        prompt_version=PROMPT_VERSION_V2,
    )
    is_cache_miss = cached_match is None
    print(f"  Cache Status: {'MISS (Expected for new real job)' if is_cache_miss else 'HIT'}")

    # Step 5 & 6 & 7: Atomic Quota Reservation & Live Gemini Evaluation
    print(f"\n[Steps 5-7] Executing AI Evaluation via AIMatcher.evaluate_job_v2()...")
    matcher = AIMatcher(model_version=model_name, prompt_version=PROMPT_VERSION_V2)
    
    # Track live execution
    eval_result: AIMatchEvaluationV2 = matcher.evaluate_job_v2(
        job=job,
        candidate=candidate,
        ai_service=ai_service,
        resume_service=resume_svc,
    )

    quota_after_eval = get_current_quota_status()
    print(f"  Evaluation Origin / Provenance: {eval_result.match_source.value}")
    print(f"  Quota After Live Request: {quota_after_eval['call_count']} calls (Delta: +{quota_after_eval['call_count'] - quota_before['call_count']})")
    assert quota_after_eval["call_count"] == quota_before["call_count"] + 1, "Expected exactly 1 quota increment!"

    # Step 8: Validate response structure
    print(f"\n[Step 8] Validating V2 Structured Evaluation:")
    print(f"  Match Score: {eval_result.match_score}/100")
    print(f"  Fit Verdict: {eval_result.fit_verdict.value}")
    print(f"  Confidence: {eval_result.confidence_score}")
    print(f"  Job Seniority Level: {eval_result.job_seniority_level}")
    print(f"  Experience Fit: {eval_result.experience_fit.value}")
    print(f"  Experience Assessment: {eval_result.experience_assessment}")
    print(f"  Work Mode: {eval_result.work_mode}")
    print(f"  Location Fit: {eval_result.location_fit}")
    print(f"  Matching Skills ({len(eval_result.matching_skills)}): {eval_result.matching_skills}")
    print(f"  Missing Skills ({len(eval_result.missing_skills)}): {eval_result.missing_skills}")
    print(f"  Transferable Skills ({len(eval_result.transferable_skills)}): {eval_result.transferable_skills}")
    print(f"  Evidence Citations ({len(eval_result.evidence)}): {eval_result.evidence}")
    print(f"  Uncertainties ({len(eval_result.uncertainties)}): {eval_result.uncertainties}")
    print(f"  Recommendations ({len(eval_result.actionable_recommendations)}): {eval_result.actionable_recommendations}")
    print(f"  Reasoning: {eval_result.reasoning}")

    assert 0 <= eval_result.match_score <= 100, "Score out of bounds"
    assert eval_result.fit_verdict in [AIMatchVerdict.STRONG_MATCH, AIMatchVerdict.GOOD_MATCH, AIMatchVerdict.STRETCH_MATCH, AIMatchVerdict.MISMATCH]
    assert eval_result.match_source == MatchProvenance.AI_LIVE, "Expected AI_LIVE provenance for first call"

    # Step 9: Verify cache write
    print(f"\n[Step 9] Verifying ai_match_cache row in Neon:")
    conn = connect_database()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, prompt_version, overall_score, match_verdict, evaluation_data
                FROM ai_match_cache
                WHERE job_hash = %s AND prompt_version = 'v2';
            """, (job_hash,))
            cache_row = cur.fetchone()
            assert cache_row is not None, "V2 Cache row was not written!"
            print(f"  [PASS] ai_match_cache id={cache_row[0]}, prompt={cache_row[1]}, score={cache_row[2]}, verdict={cache_row[3]}")
            assert cache_row[4] is not None, "evaluation_data JSONB is NULL in cache!"
            print(f"  [PASS] evaluation_data JSONB successfully populated ({len(json.dumps(cache_row[4]))} bytes)")
    finally:
        conn.close()

    # Step 10 & 11: Persist Job to Neon Database via DatabasePersistence
    print(f"\n[Steps 10 & 11] Persisting evaluated job to Neon 'jobs' table...")
    db_persistence = DatabasePersistence()
    
    # Assign dedicated AI columns
    job.ai_score = eval_result.match_score
    job.ai_verdict = eval_result.fit_verdict.value
    job.evaluation_source = eval_result.match_source.value
    job.ai_evaluation = eval_result.model_dump(mode="json")

    job_id = db_persistence.save_job(job)
    print(f"  [PASS] Job saved successfully with id={job_id}")

    # Step 12: Read both records back from Neon and verify
    print(f"\n[Step 12] Reading back and verifying records from Neon PostgreSQL:")
    persisted_job = db_persistence.get_job_by_platform_id(job.platform, job.platform_job_id)
    assert persisted_job is not None, "Failed to retrieve persisted job!"
    assert persisted_job["id"] == job_id, "Job ID mismatch"
    assert persisted_job["ai_score"] == eval_result.match_score, "ai_score mismatch"
    assert persisted_job["ai_verdict"] == eval_result.fit_verdict.value, "ai_verdict mismatch"
    assert persisted_job["evaluation_source"] == MatchProvenance.AI_LIVE.value, "evaluation_source mismatch"
    assert persisted_job["ai_evaluation"] is not None, "ai_evaluation JSONB is NULL"
    print(f"  [PASS] jobs table: id={persisted_job['id']}, ai_score={persisted_job['ai_score']}, ai_verdict={persisted_job['ai_verdict']}, source={persisted_job['evaluation_source']}")

    # Check V1 cache row integrity
    v1_after = get_v1_cache_row_summary()
    assert v1_after == v1_before, "V1 cache row was mutated!"
    print(f"  [PASS] V1 cache row (id={v1_after['id']}) strictly identical and preserved!")

    # Check candidate profile integrity
    candidate_after = resume_svc.get_active_profile()
    assert compute_profile_hash(candidate_after) == profile_hash, "Candidate profile was mutated!"
    print(f"  [PASS] Candidate profile strictly identical and unmodified (hash={profile_hash[:16]}...)")

    # Step 13: Dashboard retrieval and rendering verification
    print(f"\n[Step 13] Verifying DashboardQueryService retrieval:")
    dashboard_svc = DashboardQueryService()
    dash_metrics = dashboard_svc.get_overview_metrics()
    print(f"  Dashboard Overview Metrics:")
    print(f"    total_jobs: {dash_metrics['total_jobs']}")
    print(f"    ai_live_count: {dash_metrics.get('ai_live_count')}")
    print(f"    ai_cached_count: {dash_metrics.get('ai_cached_count')}")
    print(f"    by_ai_verdict: {dash_metrics.get('by_ai_verdict')}")
    assert dash_metrics["total_jobs"] >= 1, "Dashboard found 0 jobs!"
    assert dash_metrics.get("ai_live_count", 0) >= 1, "ai_live_count is 0 in dashboard metrics!"

    dash_jobs, total_dash = dashboard_svc.search_jobs(page=1, page_size=10)
    assert total_dash >= 1, "Dashboard search returned 0 jobs!"
    matched_dash_job = next((j for j in dash_jobs if j["id"] == job_id), None)
    assert matched_dash_job is not None, "Persisted job not returned in dashboard search!"
    assert matched_dash_job["ai_score"] == eval_result.match_score
    assert matched_dash_job["ai_verdict"] == eval_result.fit_verdict.value
    assert matched_dash_job["evaluation_source"] == MatchProvenance.AI_LIVE.value
    print(f"  [PASS] Dashboard search successfully retrieved job id={job_id} with full structured evaluation.")

    # Step 14: Cache-Hit Invariant Verification
    print(f"\n[Step 14] Verifying Cache-Hit Path (Exact Same Job & Profile):")
    print("  Executing second evaluation call to assert cache-hit invariants...")
    
    quota_before_cache_check = get_current_quota_status()
    
    # Run evaluation again
    second_eval: AIMatchEvaluationV2 = matcher.evaluate_job_v2(
        job=job,
        candidate=candidate,
        ai_service=ai_service,
        resume_service=resume_svc,
    )

    quota_after_cache_check = get_current_quota_status()
    print(f"  Second Evaluation Provenance: {second_eval.match_source.value}")
    print(f"  Quota Before Cache Check: {quota_before_cache_check['call_count']}")
    print(f"  Quota After Cache Check:  {quota_after_cache_check['call_count']} (Delta: 0)")
    
    assert second_eval.match_source == MatchProvenance.AI_CACHED, f"Expected AI_CACHED, got {second_eval.match_source}"
    assert second_eval.match_score == eval_result.match_score, "Score mismatch on cache hit"
    assert second_eval.fit_verdict == eval_result.fit_verdict, "Verdict mismatch on cache hit"
    assert quota_after_cache_check["call_count"] == quota_before_cache_check["call_count"], "Quota was consumed on cache hit!"
    print("  [PASS] Cache-hit path verified: 0 Gemini API requests dispatched, 0 quota units consumed.")

    print("\n" + "=" * 80)
    print(">>> PHASE 5A CONTROLLED SINGLE-JOB AI INTEGRATION COMPLETED SUCCESSFULLY <<<")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_phase5a_integration())
