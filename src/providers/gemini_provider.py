"""
Job Bucket — Google Gemini LLM Provider Adapter
Executes REST calls to Google Generative Language API (v1beta).
Accurately categorizes 429 quota exhaustion, 401/403 auth failures, and server errors.
"""

import json
import logging
import requests
from typing import Optional

from src.providers.base import (
    BaseLLMProvider,
    ProviderRateLimitError,
    ProviderAuthError,
    ProviderServerError,
    ProviderBadRequestError,
    ProviderNotFoundError,
    ProviderError,
)

logger = logging.getLogger(__name__)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(BaseLLMProvider):
    """
    Adapter for Google Gemini models via Generative Language REST API.
    """

    def generate_text(
        self,
        model_id: str,
        prompt: str,
        api_key: str,
        timeout_seconds: float = 15.0,
    ) -> str:
        if not api_key:
            raise ProviderAuthError("Missing Gemini API key.")

        url = f"{GEMINI_API_BASE_URL}/{model_id}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
            }
        }

        try:
            resp = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=timeout_seconds,
            )
        except requests.exceptions.Timeout:
            raise ProviderServerError(f"Gemini API request timed out after {timeout_seconds}s for {model_id}.")
        except requests.exceptions.RequestException as e:
            raise ProviderServerError(f"Gemini network connection error: {e}")

        # Classify HTTP status codes
        if resp.status_code == 200:
            try:
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ProviderError(f"Gemini returned empty candidates list for {model_id}.")
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if not parts:
                    raise ProviderError(f"Gemini returned empty parts list for {model_id}.")
                return parts[0].get("text", "")
            except Exception as e:
                raise ProviderError(f"Failed to parse Gemini 200 OK JSON: {e}")

        err_body = resp.text.lower()

        if resp.status_code == 429:
            # Check if this is a daily quota exhaustion vs a per-minute rate limit
            is_daily = "daily" in err_body or "per_day" in err_body or "quota exceeded" in err_body
            raise ProviderRateLimitError(
                f"Gemini rate limit (429) on {model_id}: {resp.text[:200]}",
                is_daily_exhaustion=is_daily,
                retry_after_seconds=60.0,
            )

        if resp.status_code in (401, 403):
            raise ProviderAuthError(f"Gemini authorization error ({resp.status_code}): {resp.text[:200]}")

        if resp.status_code == 404:
            raise ProviderNotFoundError(f"Gemini model not found (404) for {model_id}: {resp.text[:200]}")

        if resp.status_code == 400:
            raise ProviderBadRequestError(f"Gemini bad request (400) for {model_id}: {resp.text[:200]}")

        if resp.status_code in (500, 502, 503, 504):
            raise ProviderServerError(f"Gemini server error ({resp.status_code}): {resp.text[:200]}")

        raise ProviderError(f"Gemini unexpected status {resp.status_code}: {resp.text[:200]}")
