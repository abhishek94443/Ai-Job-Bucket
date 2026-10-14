"""
Job Bucket — Anthropic LLM Provider Adapter (Stub)
Ready-to-activate adapter for Anthropic Claude (Claude 3.5 Sonnet / Haiku).
"""

from src.providers.base import BaseLLMProvider, ProviderError, ProviderAuthError


class AnthropicProvider(BaseLLMProvider):
    """
    Adapter for Anthropic Claude Messages API.
    """

    def generate_text(
        self,
        model_id: str,
        prompt: str,
        api_key: str,
        timeout_seconds: float = 15.0,
    ) -> str:
        if not api_key:
            raise ProviderAuthError("Missing Anthropic API key.")
        raise ProviderError(f"Anthropic provider adapter configured for {model_id}. Connect API key to activate.")
