"""
Job Bucket — Base LLM Provider Interface
Defines the standard contract for all external AI model providers (Google, Anthropic, OpenAI, etc.).
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class ProviderError(Exception):
    """Base error for LLM provider operations."""
    pass


class ProviderRateLimitError(ProviderError):
    """Raised on HTTP 429 (RPM or RPD exhaustion)."""
    def __init__(self, message: str, is_daily_exhaustion: bool = False, retry_after_seconds: float = 60.0):
        super().__init__(message)
        self.is_daily_exhaustion = is_daily_exhaustion
        self.retry_after_seconds = retry_after_seconds


class ProviderAuthError(ProviderError):
    """Raised on HTTP 401/403 (Invalid or revoked API key)."""
    pass


class ProviderServerError(ProviderError):
    """Raised on HTTP 500/503 (Provider downtime or transient network issue)."""
    pass


class ProviderBadRequestError(ProviderError):
    """Raised on HTTP 400 (Invalid prompt or parameter)."""
    pass


class ProviderNotFoundError(ProviderError):
    """Raised on HTTP 404 (Model ID not found or deprecated by provider)."""
    pass


class BaseLLMProvider(ABC):
    """
    Abstract contract for all external LLM provider adapters.
    """

    @abstractmethod
    def generate_text(
        self,
        model_id: str,
        prompt: str,
        api_key: str,
        timeout_seconds: float = 15.0,
    ) -> str:
        """
        Executes text generation against the target model using the provided API key.
        Returns the raw string response from the model.
        """
        pass
