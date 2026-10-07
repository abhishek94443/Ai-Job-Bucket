import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.ai_enrichment_worker import (
    sanitize_and_repair_json,
    build_evaluation_prompt,
    AIEnrichmentWorker,
)
from src.providers.base import ProviderRateLimitError


def test_sanitize_and_repair_json_clean():
    raw = '{"overall_score": 85, "fit_verdict": "High Match", "why_good": ["Great match"]}'
    res = sanitize_and_repair_json(raw)
    assert res["overall_score"] == 85
    assert res["fit_verdict"] == "High Match"
    assert res["why_good"] == ["Great match"]


def test_sanitize_and_repair_json_markdown_fences():
    raw = '```json\n{"overall_score": 92, "fit_verdict": "High Match"}\n```'
    res = sanitize_and_repair_json(raw)
    assert res["overall_score"] == 92
    assert res["fit_verdict"] == "High Match"


def test_sanitize_and_repair_json_unclosed_braces():
    # Missing closing brace and trailing comma
    raw = '{"overall_score": 75, "fit_verdict": "Moderate Match", "why_good": ["Good stack"],'
    res = sanitize_and_repair_json(raw)
    assert res["overall_score"] == 75
    assert res["fit_verdict"] == "Moderate Match"


def test_sanitize_and_repair_json_regex_salvage():
    # Severely broken JSON
    raw = 'Here is the response: "overall_score": 80, "fit_verdict": "Strong Fit" and some notes'
    res = sanitize_and_repair_json(raw)
    assert res["overall_score"] == 80
    assert res["fit_verdict"] == "Strong Fit"


def test_build_evaluation_prompt():
    job = {
        "title": "Staff Backend Engineer",
        "company": "Stripe",
        "location": "Remote",
        "description_full": "We are seeking a staff backend engineer to scale billing.",
    }
    prompt = build_evaluation_prompt(
        job=job,
        candidate_profile_md="Candidate facts...",
        behavioral_profile_md="Behavioral preferences...",
    )
    assert "Staff Backend Engineer" in prompt
    assert "Stripe" in prompt
    assert "Candidate facts..." in prompt
    assert "Behavioral preferences..." in prompt


@pytest.mark.asyncio
async def test_worker_run_batch_success():
    mock_persistence = MagicMock()
    mock_persistence.claim_staged_jobs_for_enrichment.return_value = [
        {"id": 101, "title": "Senior Engineer", "company": "Acme", "description_full": "Build APIs"},
        {"id": 102, "title": "Staff Engineer", "company": "Beta", "description_full": "Scale systems"},
    ]
    mock_persistence.save_job_ai_enrichment.return_value = True

    mock_profile_service = MagicMock()
    mock_profile_service.get_masked_context_for_ai.return_value = ("Profile MD", {}, "")
    mock_profile_service.get_active_profile.return_value = {"behavioral_profile_md": "Behavioral MD"}

    mock_gateway = MagicMock()
    mock_gateway.execute_generation.return_value = {
        "text": json.dumps({"overall_score": 90, "fit_verdict": "High Match", "why_good": ["Strong fit"]}),
        "model_id": "gemini-3.5-flash-lite",
        "provider": "google",
    }

    worker = AIEnrichmentWorker(
        persistence=mock_persistence,
        profile_service=mock_profile_service,
        ai_gateway=mock_gateway,
    )

    batch_res = await worker.run_enrichment_batch(
        batch_size=2,
        concurrency=2,
        inter_request_delay_seconds=0.0,
    )

    assert batch_res["claimed_count"] == 2
    assert batch_res["enriched_count"] == 2
    assert batch_res["held_count"] == 0
    assert mock_persistence.save_job_ai_enrichment.call_count == 2


@pytest.mark.asyncio
async def test_worker_run_batch_rate_limit_backoff():
    mock_persistence = MagicMock()
    mock_persistence.claim_staged_jobs_for_enrichment.return_value = [
        {"id": 201, "title": "Backend Dev", "company": "Gamma", "description_full": "Write Python"},
    ]
    mock_persistence.mark_jobs_quota_held.return_value = 1

    mock_profile_service = MagicMock()
    mock_profile_service.get_masked_context_for_ai.return_value = ("Profile MD", {}, "")
    mock_profile_service.get_active_profile.return_value = {"behavioral_profile_md": "Behavioral MD"}

    mock_gateway = MagicMock()
    mock_gateway.execute_generation.side_effect = ProviderRateLimitError("429 Resource Exhausted")

    worker = AIEnrichmentWorker(
        persistence=mock_persistence,
        profile_service=mock_profile_service,
        ai_gateway=mock_gateway,
    )

    batch_res = await worker.run_enrichment_batch(
        batch_size=1,
        concurrency=1,
        inter_request_delay_seconds=0.0,
    )

    assert batch_res["claimed_count"] == 1
    assert batch_res["enriched_count"] == 0
    assert batch_res["held_count"] == 1
    mock_persistence.mark_jobs_quota_held.assert_called_once_with([201], reason="Rate limit 429 encountered during batch")


def test_sanitize_and_repair_json_pack():
    from src.ai_enrichment_worker import sanitize_and_repair_json_pack

    raw_array = json.dumps([
        {"job_id": 10, "overall_score": 88, "fit_verdict": "High Match"},
        {"job_id": 20, "overall_score": 75, "fit_verdict": "Moderate Match"},
    ])
    res = sanitize_and_repair_json_pack(raw_array, expected_job_ids=[10, 20])
    assert len(res) == 2
    assert res[0]["job_id"] == 10
    assert res[0]["overall_score"] == 88
    assert res[1]["job_id"] == 20
    assert res[1]["overall_score"] == 75


@pytest.mark.asyncio
async def test_worker_multi_job_pack_2_jobs():
    mock_persistence = MagicMock()
    mock_persistence.claim_staged_jobs_for_enrichment.return_value = [
        {"id": 301, "title": "Role A", "company": "Co A", "description_full": "Desc A"},
        {"id": 302, "title": "Role B", "company": "Co B", "description_full": "Desc B"},
    ]
    mock_persistence.save_job_ai_enrichment.return_value = True

    mock_profile_service = MagicMock()
    mock_profile_service.get_masked_context_for_ai.return_value = ("Profile MD", {}, "")
    mock_profile_service.get_active_profile.return_value = {"behavioral_profile_md": "Behavioral MD"}

    mock_gateway = MagicMock()
    mock_gateway.execute_generation.return_value = {
        "text": json.dumps([
            {"job_id": 301, "overall_score": 90, "fit_verdict": "High Match"},
            {"job_id": 302, "overall_score": 80, "fit_verdict": "Moderate Match"},
        ]),
        "model_id": "gemini-3.5-flash-lite",
        "provider": "google",
    }

    worker = AIEnrichmentWorker(
        persistence=mock_persistence,
        profile_service=mock_profile_service,
        ai_gateway=mock_gateway,
    )

    batch_res = await worker.run_enrichment_batch(
        jobs_per_call=2,
        concurrency=1,
        inter_request_delay_seconds=0.0,
    )

    assert batch_res["claimed_count"] == 2
    assert batch_res["enriched_count"] == 2
    assert batch_res["packs_count"] == 1  # 2 jobs packed into 1 call!
    assert mock_gateway.execute_generation.call_count == 1  # Exactly 1 LLM request!
    assert mock_persistence.save_job_ai_enrichment.call_count == 2

