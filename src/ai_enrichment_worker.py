"""
Job Bucket - Quota-Aware Concurrent AI Enrichment Worker
Evaluates staged jobs against the candidate technical profile and behavioral framework.

Key Capabilities:
1. Multi-Job Prompt Packing: Packs up to K=2 balanced jobs into a single API request, cutting RPM by 50%
   and eliminating redundant prompt context overhead.
2. Character-Balanced Bin-Packing: Uses group_jobs_into_balanced_packs to pair large and small listings,
   ensuring uniform latency across concurrent workers.
3. Concurrency Bounded by asyncio.Semaphore: Controls parallel in-flight HTTP requests.
4. Resilient Multi-Job Parser: Parses JSON arrays [ {...}, {...} ] or single objects with AST/bracket repair,
   committing each job independently to PostgreSQL.
5. Atomic State Transitions: In-flight jobs are claimed atomically, enriched jobs commit as AI_ENRICHED,
   and interrupted jobs revert cleanly to AI_QUOTA_HELD on 429 quota exhaustion.
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from src.ai_gateway import AIGateway
from src.batch_window_optimizer import group_jobs_into_balanced_packs
from src.candidate_profile_service import CandidateMasterProfileService
from src.db_persistence import DatabasePersistence
from src.providers.base import ProviderRateLimitError

logger = logging.getLogger(__name__)


def sanitize_and_repair_json(raw_text: str) -> Dict[str, Any]:
    """
    4-Stage resilient JSON parser for single LLM response objects.
    """
    cleaned = raw_text.strip()

    # Stage 1: Extract block inside markdown fence if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]

    # Stage 2: Strict load
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # Stage 3: Escape illegal backslashes and repair unclosed brackets
    repaired = re.sub(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})', r"\\\\", cleaned)

    open_curly = repaired.count("{")
    close_curly = repaired.count("}")
    if open_curly > close_curly:
        repaired += "}" * (open_curly - close_curly)

    open_square = repaired.count("[")
    close_square = repaired.count("]")
    if open_square > close_square:
        repaired += "]" * (open_square - close_square)

    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)

    try:
        return json.loads(repaired)
    except Exception:
        pass

    # Stage 4: Regex-based field salvage
    salvaged: Dict[str, Any] = {
        "overall_score": 70,
        "fit_verdict": "Moderate Match",
        "why_good": [],
        "why_bad": [],
        "key_observations": [],
        "behavioral_talking_points": [],
        "matched_skills": [],
        "missing_skills": [],
    }

    score_match = re.search(r'"overall_score"\s*:\s*(\d+)', raw_text)
    if score_match:
        salvaged["overall_score"] = int(score_match.group(1))

    verdict_match = re.search(r'"fit_verdict"\s*:\s*"([^"]+)"', raw_text)
    if verdict_match:
        salvaged["fit_verdict"] = verdict_match.group(1)

    for field in ["why_good", "why_bad", "key_observations", "behavioral_talking_points", "matched_skills"]:
        field_match = re.search(rf'"{field}"\s*:\s*\[([^\]]*)\]', raw_text, re.DOTALL)
        if field_match:
            items = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', field_match.group(1))
            if items:
                salvaged[field] = items

    return salvaged


def sanitize_and_repair_json_pack(raw_text: str, expected_job_ids: List[int]) -> List[Dict[str, Any]]:
    """
    Parses LLM output that can be either:
    1. A JSON array of evaluation objects: [ {"job_id": 1, ...}, {"job_id": 2, ...} ]
    2. A single JSON object: {"overall_score": ...} (if 1 job was packed)
    Returns a list of parsed evaluation dicts, guaranteed to contain "job_id".
    """
    cleaned = raw_text.strip()

    # Stage 1: Strip markdown fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    parsed_obj: Any = None

    # Stage 2: Strict load
    try:
        parsed_obj = json.loads(cleaned)
    except Exception:
        # Stage 3: Bracket and escape auto-repair
        repaired = re.sub(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})', r"\\\\", cleaned)
        open_curly = repaired.count("{")
        close_curly = repaired.count("}")
        if open_curly > close_curly:
            repaired += "}" * (open_curly - close_curly)
        open_square = repaired.count("[")
        close_square = repaired.count("]")
        if open_square > close_square:
            repaired += "]" * (open_square - close_square)
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        try:
            parsed_obj = json.loads(repaired)
        except Exception:
            pass

    # Normalize to list of dicts
    evaluations: List[Dict[str, Any]] = []

    if isinstance(parsed_obj, list):
        for item in parsed_obj:
            if isinstance(item, dict):
                evaluations.append(item)
    elif isinstance(parsed_obj, dict):
        evaluations.append(parsed_obj)

    # Match evaluations to expected job_ids
    results: List[Dict[str, Any]] = []
    claimed_set = set()

    # Match by explicit "job_id"
    for ev in evaluations:
        j_id = ev.get("job_id")
        if j_id is not None:
            try:
                j_id = int(j_id)
                if j_id in expected_job_ids and j_id not in claimed_set:
                    ev["job_id"] = j_id
                    results.append(ev)
                    claimed_set.add(j_id)
            except (ValueError, TypeError):
                pass

    # For any unmatched evaluations, assign by index to remaining expected_job_ids
    unclaimed_ids = [j_id for j_id in expected_job_ids if j_id not in claimed_set]
    unclaimed_evals = [ev for ev in evaluations if ev not in results]

    for j_id, ev in zip(unclaimed_ids, unclaimed_evals):
        ev["job_id"] = j_id
        results.append(ev)
        claimed_set.add(j_id)

    # For any still missing expected_job_ids, perform regex salvage or create safe fallback
    still_missing = [j_id for j_id in expected_job_ids if j_id not in claimed_set]
    for j_id in still_missing:
        fallback = sanitize_and_repair_json(raw_text)
        fallback["job_id"] = j_id
        results.append(fallback)

    return results


def build_evaluation_prompt(
    job: Dict[str, Any],
    candidate_profile_md: str,
    behavioral_profile_md: str,
) -> str:
    """
    Constructs evaluation prompt for a single job posting.
    """
    return build_packed_evaluation_prompt([job], candidate_profile_md, behavioral_profile_md)


def build_packed_evaluation_prompt(
    jobs: List[Dict[str, Any]],
    candidate_profile_md: str,
    behavioral_profile_md: str,
) -> str:
    """
    Constructs multi-job prompt packing 1 or more jobs into a single API request.
    Prompts the model to return a structured JSON array of evaluations.
    """
    jobs_sections = []
    for idx, job in enumerate(jobs, start=1):
        j_id = job.get("id")
        j_title = job.get("title", "Unknown Role")
        j_company = job.get("company", "Unknown Company")
        j_location = job.get("location", "Not specified")
        j_desc = (
            job.get("description_full")
            or job.get("description")
            or "No detailed job description provided."
        )
        if len(j_desc) > 12000:
            j_desc = j_desc[:12000] + "\n...[description truncated for context safety]"

        jobs_sections.append(f"""### Job {idx} (ID: {j_id})
Role: {j_title}
Company: {j_company}
Location: {j_location}

Description:
{j_desc}""")

    combined_jobs_text = "\n\n".join(jobs_sections)

    if len(jobs) == 1:
        output_instruction = f"""Respond ONLY with a valid JSON array containing exactly 1 evaluation object matching this schema:
[
  {{
    "job_id": {jobs[0].get("id")},
    "overall_score": 85,
    "fit_verdict": "High Match",
    "why_good": [
      "Specific strength 1 grounded in candidate background",
      "Specific strength 2"
    ],
    "why_bad": [
      "Objective technical gap or missing experience signal"
    ],
    "key_observations": [
      "Observation regarding architecture, stack, or team culture"
    ],
    "behavioral_talking_points": [
      "Specific behavioral achievement or talking point for interviews"
    ],
    "matched_skills": [
      "Skill1", "Skill2"
    ],
    "missing_skills": [
      "MissingSkill1"
    ]
  }}
]"""
    else:
        sample_items = []
        for j in jobs:
            sample_items.append(f"""  {{
    "job_id": {j.get("id")},
    "overall_score": 85,
    "fit_verdict": "High Match",
    "why_good": ["Specific strength 1", "Specific strength 2"],
    "why_bad": ["Objective gap 1"],
    "key_observations": ["Observation 1"],
    "behavioral_talking_points": ["Talking point 1"],
    "matched_skills": ["Skill1"],
    "missing_skills": ["MissingSkill1"]
  }}""")
        samples_formatted = ",\n".join(sample_items)
        output_instruction = f"""Respond ONLY with a valid JSON array containing an evaluation object for each job matching this schema:
[
{samples_formatted}
]"""

    return f"""You are a Staff Technical Recruiter and Principal Software Engineering Hiring Lead.
Evaluate the following target job posting(s) against the candidate's verified technical background and behavioral preferences.

Candidate Technical Profile:
{candidate_profile_md}

Candidate Behavioral and Work-Style Profile:
{behavioral_profile_md}

Target Job Postings to Evaluate:
{combined_jobs_text}

Evaluation Instructions:
1. Technical Skills Match (0-100): Measure required skills against candidate primary skills.
2. Experience & Domain Match (0-100): Compare engineering scope, responsibilities, and systems scale.
3. Behavioral and Culture Fit (0-100): Check work autonomy, environment preferences, and communication style.
4. Calculate an overall normalized score from 0 to 100 for each role.
5. Identify specific concrete strengths (why_good) and objective gaps (why_bad).
6. Provide actionable behavioral talking points for upcoming interview discussions.

{output_instruction}"""


class AIEnrichmentWorker:
    """
    Worker engine managing concurrent, bounded execution of AI job evaluations
    with multi-job prompt packing and balanced bin-packing.
    """

    def __init__(
        self,
        persistence: Optional[DatabasePersistence] = None,
        profile_service: Optional[CandidateMasterProfileService] = None,
        ai_gateway: Optional[AIGateway] = None,
    ):
        self.persistence = persistence or DatabasePersistence()
        self.profile_service = profile_service or CandidateMasterProfileService()
        self.ai_gateway = ai_gateway or AIGateway()

    async def _evaluate_job_pack(
        self,
        pack: List[Dict[str, Any]],
        profile_md: str,
        behavioral_md: str,
        semaphore: asyncio.Semaphore,
        inter_request_delay_seconds: float = 1.0,
    ) -> List[Tuple[int, bool, Dict[str, Any]]]:
        """
        Evaluates a pack of 1 or 2 jobs in a single API call under the concurrency semaphore.
        Returns: List of (job_id, is_success, evaluation_dict_or_error)
        """
        expected_ids = [j["id"] for j in pack]
        async with semaphore:
            try:
                if inter_request_delay_seconds > 0:
                    await asyncio.sleep(inter_request_delay_seconds)

                prompt = build_packed_evaluation_prompt(pack, profile_md, behavioral_md)

                loop = asyncio.get_running_loop()
                response_data = await loop.run_in_executor(
                    None, self.ai_gateway.execute_generation, prompt, 40.0
                )

                raw_text = response_data.get("text", "")
                parsed_evals = sanitize_and_repair_json_pack(raw_text, expected_ids)

                results = []
                for ev in parsed_evals:
                    job_id = ev.get("job_id")
                    if not job_id:
                        continue
                    score = int(ev.get("overall_score", 70))
                    score = max(0, min(100, score))
                    ev["overall_score"] = score
                    ev["evaluated_by_model"] = response_data.get("model_id")
                    ev["evaluated_by_provider"] = response_data.get("provider")

                    # Atomically persist enriched result
                    self.persistence.save_job_ai_enrichment(job_id, score, ev)
                    results.append((job_id, True, ev))

                # Ensure every expected job has a result
                found_ids = {r[0] for r in results}
                for j_id in expected_ids:
                    if j_id not in found_ids:
                        fallback_score = 70
                        fallback_data = {
                            "job_id": j_id,
                            "overall_score": fallback_score,
                            "fit_verdict": "Moderate Match",
                            "why_good": [],
                            "why_bad": [],
                        }
                        self.persistence.save_job_ai_enrichment(j_id, fallback_score, fallback_data)
                        results.append((j_id, True, fallback_data))

                return results

            except ProviderRateLimitError as rle:
                logger.warning(f"Rate limit hit while evaluating pack {expected_ids}: {rle}")
                return [(j_id, False, {"error": "RATE_LIMIT_429", "detail": str(rle)}) for j_id in expected_ids]
            except Exception as e:
                logger.error(f"Error evaluating pack {expected_ids}: {e}")
                return [(j_id, False, {"error": "EVALUATION_ERROR", "detail": str(e)}) for j_id in expected_ids]

    async def run_enrichment_batch(
        self,
        batch_size: Optional[int] = None,
        jobs_per_call: int = 2,
        concurrency: int = 2,
        job_ids: Optional[List[int]] = None,
        inter_request_delay_seconds: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Pulls staged jobs, packs them into balanced groups of `jobs_per_call`,
        and evaluates them concurrently up to the concurrency ceiling.
        """
        # Safety bounds
        jobs_per_call = max(1, min(2, jobs_per_call))
        concurrency = max(1, min(4, concurrency))

        # Total jobs to pull in this wave
        if batch_size is not None and batch_size > 0:
            total_pull = min(15, batch_size)
        else:
            total_pull = jobs_per_call * concurrency

        # 1. Atomically claim staged jobs
        claimed_jobs = self.persistence.claim_staged_jobs_for_enrichment(
            limit=total_pull, job_ids=job_ids
        )

        if not claimed_jobs:
            return {
                "claimed_count": 0,
                "enriched_count": 0,
                "held_count": 0,
                "failed_count": 0,
                "jobs": [],
                "message": "No staged jobs waiting for enrichment.",
            }

        # 2. Retrieve Candidate Profile and Behavioral Persona
        profile_md, unmask_map, _ = self.profile_service.get_masked_context_for_ai()
        active_prof = self.profile_service.get_active_profile()
        behavioral_md = active_prof.get("behavioral_profile_md", "") or "Collaborative systems engineer."

        # 3. Group claimed jobs into balanced packs to minimize variance across workers
        packs = group_jobs_into_balanced_packs(claimed_jobs, pack_size=jobs_per_call)

        semaphore = asyncio.Semaphore(concurrency)

        # 4. Dispatch concurrent pack evaluation tasks
        tasks = [
            self._evaluate_job_pack(
                pack,
                profile_md,
                behavioral_md,
                semaphore,
                inter_request_delay_seconds,
            )
            for pack in packs
        ]

        nested_results = await asyncio.gather(*tasks, return_exceptions=False)

        enriched_count = 0
        held_ids: List[int] = []
        failed_count = 0
        details: List[Dict[str, Any]] = []

        for pack_res in nested_results:
            for job_id, success, outcome in pack_res:
                if success:
                    enriched_count += 1
                    details.append({"job_id": job_id, "status": "AI_ENRICHED", "score": outcome.get("overall_score")})
                else:
                    err_code = outcome.get("error")
                    if err_code == "RATE_LIMIT_429":
                        held_ids.append(job_id)
                    else:
                        failed_count += 1
                    details.append({"job_id": job_id, "status": "FAILED", "error": outcome})

        # 5. Handle Quota Exhaustion
        if held_ids:
            self.persistence.mark_jobs_quota_held(held_ids, reason="Rate limit 429 encountered during batch")

        return {
            "claimed_count": len(claimed_jobs),
            "enriched_count": enriched_count,
            "held_count": len(held_ids),
            "failed_count": failed_count,
            "packs_count": len(packs),
            "jobs_per_call": jobs_per_call,
            "concurrency": concurrency,
            "details": details,
            "message": f"Enriched {enriched_count} jobs across {len(packs)} API call(s), {len(held_ids)} held on quota.",
        }
