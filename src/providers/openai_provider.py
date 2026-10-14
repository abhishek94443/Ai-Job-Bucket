"""
Job Bucket — OpenAI LLM Provider Adapter (Stub)
Ready-to-activate adapter for OpenAI (GPT-4o, GPT-4o-mini).
"""

from src.providers.base import BaseLLMProvider, ProviderError, ProviderAuthError


class OpenAIProvider(BaseLLMProvider):
    """
    Adapter for OpenAI Chat Completions API.
    """

    def generate_text(
        self,
        model_id: str,
        prompt: str,
        api_key: str,
        timeout_seconds: float = 15.0,
    ) -> str:
        if not api_key:
            raise ProviderAuthError("Missing OpenAI API key.")
        raise ProviderError(f"OpenAI provider adapter configured for {model_id}. Connect API key to activate.")
