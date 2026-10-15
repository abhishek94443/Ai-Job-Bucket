#!/usr/bin/env python3
"""
Job Bucket — Phase 3D.2: Single-Job Live-Matching Preflight CLI.

This script provides an explicit, disabled-by-default entry point for testing
a single job listing against the verified Google Gemini API.

SAFETY CONSTRAINTS:
1. OPT-IN MANDATORY: Execution will immediately abort unless `--enable-live-ai-test` is passed.
2. AT MOST ONE REQUEST: Executes exactly 0 or 1 LLM request per run. Never performs bulk matching.
3. CACHE SHORTCUT: If a cached evaluation exists, returns it immediately without provider dispatch.
4. DETERMINISTIC REJECTION PRECEDENCE: Rejects any job with decision == REJECTED prior to dispatch.
5. ZERO PII TRANSMISSION: Uses PrivacyScrubber to redact job descriptions and candidate identity.
6. PERSISTENT QUOTA ENFORCEMENT: Reserves 1 quota unit in PostgreSQL before HTTP dispatch.
7. TRANSACTION ISOLATION: Closes all database connections before dispatching the HTTP call.
8. COST & PRIVACY NOTICE:
   - The application quota cap (50 calls/day) is an application-level guardrail, not a guarantee
     of zero billing if the user's Google Cloud project has billing enabled.
   - Verify your Google AI Studio plan & billing status at https://aistudio.google.com/.
   - In Google AI Studio Free Tier, prompt and response data may be processed and reviewed
     by human reviewers to train Google models.
   - PII scrubbing redacts direct identifiers (names, emails, phones, links), but specific
     career history or niche company combinations may still be semi-identifiable.
   - Never falls back to a paid model or third-party provider.
"""

import argparse
import logging
import os
import sys

# Ensure repository root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.ai_service import (
    GeminiAIService,
    DEFAULT_GEMINI_MODEL,
    AIConfigurationError,
    AIQuotaExceededError,
    AIDatabaseConnectivityError,
    AIDailyQuotaExhaustedError,
    AIServiceError,
    resolve_model_name,
)
from src.resume_service import ResumeService
from src.ai_live_preflight import (
    execute_single_job_live_preflight,
    load_single_job_for_preflight,
    SingleJobPreflightResult,
)
from src.models import DeterministicDecision

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("single_job_live_test")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Job Bucket: Single-Job Gemini Live-Matching Preflight Test",
        epilog="IMPORTANT: This test will consume 1 persistent quota unit if uncached. Free-tier terms apply.",
    )
    parser.add_argument(
        "--enable-live-ai-test",
        action="store_true",
        default=False,
        help="Explicit operator authorization flag to execute the single-job live test.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional Gemini model override (defaults to GEMINI_MODEL or gemini-3.5-flash-lite).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Inspect payload and candidate without making external API calls or database writes.",
    )
    parser.add_argument(
        "--use-sample",
        action="store_true",
        default=False,
        help="Use an in-memory sample eligible job listing for dry-run inspection.",
    )
    return parser


def run_preflight_cli(args) -> int:
    # 1. Check opt-in authorization gate
    if not args.enable_live_ai_test and not args.dry_run:
        print("=" * 80)
        print("JOB BUCKET: SINGLE-JOB LIVE AI PREFLIGHT GATE")
        print("=" * 80)
        print("STATUS: DISABLED BY DEFAULT (Safety Gate Active)")
        print()
        print("Execution was refused because '--enable-live-ai-test' was not supplied.")
        print()
        print("To preview the outgoing request without making API calls or database writes:")
        print("    python scripts/single_job_live_test.py --dry-run")
        print()
        print("To authorize a single-job live test, explicitly invoke:")
        print("    python scripts/single_job_live_test.py --enable-live-ai-test")
        print()
        print("COST & DATA HANDLING DISCLOSURE:")
        print("1. Application Quota: Enforces 50 calls/day cap in PostgreSQL ai_daily_usage.")
        print("   Note: The app cap is not a billing guarantee if your Google Cloud project")
        print("   has billing attached. Verify your plan at https://aistudio.google.com/.")
        print("2. Google AI Studio Free Tier Terms: Prompt and response data may be used")
        print("   to train Google products. Candidate PII is scrubbed before transmission,")
        print("   but technical work history facts are evaluated by the model.")
        print("3. Deterministic Invariant: Deterministically rejected jobs will never be evaluated.")
        print("=" * 80)
        return 1

    # 2. Validate API key and Model configuration
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        if not args.dry_run:
            print("ERROR: GEMINI_API_KEY is not configured in the environment or secrets.")
            print("Ensure GEMINI_API_KEY is set in your environment or .env file before authorizing.")
            return 1
        api_key = None


    try:
        model_name = resolve_model_name(args.model)
    except AIConfigurationError as exc:
        print(f"ERROR: Model configuration invalid: {exc}")
        return 1

    # 3. Load active candidate profile
    resume_svc = ResumeService()
    try:
        has_active = resume_svc.has_active_profile()
    except Exception as exc:
        print(f"ERROR: Database check for active profile failed: {exc}")
        return 1

    if not has_active:
        print("ERROR: No active candidate profile found in Neon database.")
        print("Please upload and activate a candidate profile before running preflight.")
        return 1

    candidate = resume_svc.get_active_profile()
    if not candidate.languages and not candidate.frameworks:
        print("ERROR: Active candidate profile contains zero extracted technical skills.")
        return 1

    # 4. Obtain exactly one real job listing
    if getattr(args, "use_sample", False):
        from src.models import (
            JobRecord,
            ValidationEvidence,
            JobStatus,
            ApplicationType,
            RelevanceScore,
            ExperienceLevel,
            FitEligibility,
            ConfidenceLevel,
            DeterministicEligibility,
        )
        job = JobRecord(
            platform="sample",
            platform_job_id="sample-job-001",
            title="Java Backend Developer",
            company="CloudTech Systems",
            location="Bengaluru, Karnataka",
            url="https://example.com/jobs/sample-job-001",
            normalized_url="https://example.com/jobs/sample-job-001",
            description_snippet=(
                "CloudTech is looking for a Java Backend Developer with 1-3 years of experience. "
                "Must have strong skills in Java, Spring Boot, Microservices, and PostgreSQL. "
                "Experience with Kafka or Redis is a plus. Contact recruiter@cloudtech.com or call +91-9876543210."
            ),
            validation=ValidationEvidence(
                job_status=JobStatus.ACTIVE,
                application_type=ApplicationType.COMPANY_DIRECT,
                evidence_source="sample_spec",
                evidence_notes="Sample listing for dry-run inspection",
                application_url="https://example.com/jobs/sample-job-001",
            ),
            relevance=RelevanceScore(
                total_score=85,
                experience_level=ExperienceLevel.MID_LEVEL,
                eligibility=FitEligibility.HIGH_FIT,
                eligibility_reason="Deterministic alignment verified",
                confidence_level=ConfidenceLevel.HIGH,
                confidence_score=0.9,
                matched_skills=["Java", "Spring Boot", "PostgreSQL"],
            ),
        )
        from src.eligibility import DeterministicJobEvaluator
        evaluator = DeterministicJobEvaluator()
        job.deterministic_eligibility = evaluator.evaluate(job, candidate)
    else:
        print(f"Loading single candidate job listing for profile: {candidate.target_title}...")
        job = load_single_job_for_preflight(profile=candidate)
        if job is None:
            print("ERROR: No eligible job listing found from persisted storage or discovery.")
            print("Ensure at least one job has been discovered or stored (or use --use-sample for offline test).")
            return 1

    # 5. Pre-dispatch check: deterministic rejection precedence
    det = job.deterministic_eligibility
    if det and det.decision == DeterministicDecision.REJECTED:
        reasons = "; ".join(det.rejection_reasons) if det.rejection_reasons else "Hard cutoff"
        print(f"ERROR: Selected job '{job.title}' @ '{job.company}' was deterministically REJECTED ({reasons}).")
        print("Deterministic rejections are impossible to override and must never be evaluated by AI.")
        return 1

    # 6. Dry run check
    if args.dry_run:
        import json
        from src.ai_matcher import AIMatcher
        matcher = AIMatcher(model_version=model_name)
        payload = matcher.build_scrubbed_payload(job, candidate)
        system_inst, effective_prompt = matcher.build_effective_prompt(candidate, job)

        # Cache check
        cached_eval = resume_svc.get_cached_match(
            profile_hash=payload["profile_hash"],
            platform=job.platform,
            platform_job_id=job.platform_job_id,
            job_hash=payload["job_hash"],
            model_version=model_name,
            prompt_version=matcher.prompt_version,
        )

        if api_key:
            masked_key = f"***...{api_key[-4:]}" if len(api_key) > 4 else "***"
        else:
            masked_key = "[NOT CONFIGURED IN ENV]"
        endpoint_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

        print("=" * 80)
        print("JOB BUCKET: LIVE-MATCHING PREFLIGHT DRY-RUN AUDIT")
        print("=" * 80)
        print("[CONFIG & ROUTING]")
        print(f"  Target Model:       {model_name}")
        print(f"  Provider Endpoint:  {endpoint_url}")
        print(f"  Auth Header:        x-goog-api-key: {masked_key} (Passed via HTTP header, never in URL)")
        print(f"  Request Bounds:     Timeout: 15.0s | Max Requests: 1 | Automatic Retries: 0")
        print(f"  Model Fallback:     None (Strict zero silent fallback)")
        print()
        print("[CACHE & QUOTA STATUS]")
        if cached_eval is not None:
            print("  Cache State:        HIT (Existing validated match found in ai_match_cache)")
            print(f"  Existing Verdict:   {cached_eval.fit_verdict} (Score: {cached_eval.match_score}/100)")
            print("  Live Call Impact:   0 API requests dispatched | 0 quota units consumed")
        else:
            print("  Cache State:        MISS (No prior evaluation found)")
            print("  Live Call Impact:   1 persistent quota unit will be reserved (ai_daily_usage table)")
            print("                      Exactly 1 HTTP request will be dispatched to Gemini")
        print()
        print("[JOB LISTING UNDER EVALUATION]")
        print(f"  Title:              {job.title}")
        print(f"  Company:            {job.company}")
        print(f"  Platform/ID:        {job.platform} / {job.platform_job_id}")
        print(f"  Location:           {job.location or 'N/A'}")
        print(f"  Eligibility:        {det.decision.value if det else 'UNKNOWN'}")
        if det and det.review_reasons:
            print(f"  Review Reasons:     {'; '.join(det.review_reasons)}")
        if det and det.reasons:
            print(f"  Eligible Factors:   {'; '.join(det.reasons)}")
        print(f"  Redactions Applied: {payload.get('redactions_applied', {})}")
        print(f"  Scrubbed Desc:      {payload['description_preview']}")
        print()
        print("[SYSTEM INSTRUCTION (BEHAVIORAL DIRECTIVES)]")
        print(f"  {system_inst}")
        print()
        print("[SANITIZED CANDIDATE PAYLOAD (ZERO PII)]")
        print(json.dumps(payload["candidate_summary"], indent=4))
        print()
        print("[EFFECTIVE USER PROMPT PREVIEW]")
        print("-" * 40)
        print(effective_prompt)
        print("-" * 40)
        print()
        print("[OUTGOING GENERATION CONFIG & SCHEMA]")
        print("  response_mime_type: application/json")
        print("  temperature:        0.1")
        print("  required schema:    AIMatchEvaluation (match_score, fit_verdict, matching_evidence,")
        print("                                         missing_requirements, experience_assessment, reasoning)")

        print()
        print("[AUDIT GUARANTEES]")
        print("  - LLM Calls Dispatched:  0")
        print("  - Quota Reserved:        0")
        print("  - Database Writes:       0")
        print("  - DB Reads Executed:     candidate_profiles (active profile check), ai_match_cache (lookup)")
        print("=" * 80)
        return 0

    # 7. Execute single-job live preflight test
    ai_service = GeminiAIService(api_key=api_key, model_name=model_name)
    try:
        result: SingleJobPreflightResult = execute_single_job_live_preflight(
            job=job,
            candidate=candidate,
            enable_live_test=True,
            resume_service=resume_svc,
            ai_service=ai_service,
        )
    except AIDatabaseConnectivityError as exc:
        print(f"ERROR: Persistent quota tracking database unavailable: {exc}")
        return 1
    except AIDailyQuotaExhaustedError as exc:
        print(f"ERROR: Daily AI call quota limit reached (50 calls/day): {exc}")
        return 1
    except AIQuotaExceededError as exc:
        print(f"ERROR: Persistent quota limit reached: {exc}")
        return 1
    except AIConfigurationError as exc:
        print(f"ERROR: AI Configuration error: {exc}")
        return 1
    except AIServiceError as exc:
        print(f"ERROR: Provider error during execution: {exc}")
        return 1
    except Exception as exc:
        clean_msg = str(exc)
        if api_key in clean_msg:
            clean_msg = clean_msg.replace(api_key, "[REDACTED_API_KEY]")
        print(f"ERROR: Unexpected preflight error: {clean_msg}")
        return 1

    # 8. Print safe, sanitized output
    print("=" * 80)
    print("JOB BUCKET: SINGLE-JOB LIVE PREFLIGHT RESULT")
    print("=" * 80)
    print(f"Job Title:       {result.job_title}")
    print(f"Company:         {result.company}")
    print(f"Platform:        {result.platform} (ID: {result.platform_job_id})")
    print(f"Eligibility:     {result.eligibility_decision}")
    print(f"Source Status:   {'CACHED (0 external calls, 0 quota used)' if result.from_cache else 'LIVE PROVIDER CALL (1 quota consumed)'}")
    print(f"Model ID:        {result.model_version}")
    if result.evaluation:
        print(f"Match Score:     {result.evaluation.match_score}/100")
        print(f"Fit Verdict:     {result.evaluation.fit_verdict}")
        print(f"Confidence:      {result.evaluation.confidence_score}")
        print(f"Reasoning:       {result.evaluation.reasoning}")
        if result.evaluation.matching_evidence:
            print(f"Evidence:        {', '.join(result.evaluation.matching_evidence[:3])}")
    print("-" * 80)
    print(f"Status:          {result.status_message}")
    print("=" * 80)
    return 0


def main():
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(run_preflight_cli(args))


if __name__ == "__main__":
    main()
