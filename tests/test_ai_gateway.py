"""
Unit & Integration tests for AIGateway Engine (Step 2).
Verifies:
1. Model Registry: Retrieves models from PostgreSQL with proper priorities and limits.
2. In-Memory RPM Sliding Window: Rate limits within 60s and recovers automatically.
3. Manual RPD Synchronization: Updates calls_today on demand.
4. Cascading Waterfall: When Model 1 is exhausted (e.g. daily quota reached or 429),
   gateway automatically cascades to Model 2 and successfully delivers the request.
5. All Models Exhausted Error: Raises clean exception when entire waterfall is exhausted.
"""

from unittest.mock import MagicMock, patch
import pytest

from src.ai_gateway import AIGateway, AllModelsExhaustedError
from src.env_manager import EnvKeyManager
from src.providers.base import ProviderRateLimitError, ProviderAuthError, ProviderNotFoundError


@pytest.fixture
def mock_env_manager():
    mgr = MagicMock(spec=EnvKeyManager)
    mgr.get_key.side_effect = lambda alias: "test-valid-api-key-12345"
    return mgr


def test_ai_gateway_fetches_models_from_db():
    gateway = AIGateway()
    models = gateway.get_registered_models()

    assert len(models) >= 5
    # Priority order check
    priorities = [m["priority"] for m in models]
    assert priorities == sorted(priorities)
    # Check seeded models
    model_ids = [m["model_id"] for m in models]
    assert "gemini-3.5-flash-lite" in model_ids
    assert "gemini-3.1-flash-lite" in model_ids
    assert "gemini-3.7-flash" in model_ids


def test_ai_gateway_manual_rpd_update():
    gateway = AIGateway()
    target_model = "gemini-3.7-flash"
    alias = "GEMINI_API_KEY"

    # Set calls today to 17
    success = gateway.update_model_used_calls(target_model, alias, calls_today=17)
    assert success is True

    # Verify updated in DB
    models = gateway.get_registered_models()
    m_row = next(m for m in models if m["model_id"] == target_model and m["api_key_alias"] == alias)
    assert m_row["calls_today"] == 17

    # Reset back to 0
    gateway.update_model_used_calls(target_model, alias, calls_today=0)
    models = gateway.get_registered_models()
    m_row = next(m for m in models if m["model_id"] == target_model and m["api_key_alias"] == alias)
    assert m_row["calls_today"] == 0


def test_ai_gateway_in_memory_rpm_cache():
    gateway = AIGateway()
    model = "gemini-3.5-flash-lite"
    alias = "KEY_TEST"

    # Limit 2 RPM
    assert gateway._is_rpm_available(model, alias, rpm_limit=2) is True
    gateway._record_call_memory(model, alias)
    assert gateway._is_rpm_available(model, alias, rpm_limit=2) is True
    gateway._record_call_memory(model, alias)
    # Third call should hit RPM limit in memory
    assert gateway._is_rpm_available(model, alias, rpm_limit=2) is False

    # Trip cooldown
    gateway.trip_cooldown(model, alias, duration_seconds=10.0)
    assert gateway._is_cooling_down(model, alias) is True


def test_ai_gateway_exhausts_model1_and_cascades_to_model2(mock_env_manager):
    """
    CRUCIAL TEST:
    Model 1 (gemini-3.5-flash-lite) is exhausted / hits 429.
    Gateway automatically cascades to Model 2 (gemini-3.1-flash-lite).
    Sends 1 request to Model 2 and succeeds!
    """
    gateway = AIGateway(env_manager=mock_env_manager)

    mock_google_provider = MagicMock()

    # Model 1 fails with 429 RateLimit, Model 2 succeeds!
    def fake_generate(model_id, prompt, api_key, timeout_seconds=15.0):
        if model_id == "gemini-3.5-flash-lite":
            raise ProviderRateLimitError("429 Quota exhausted for model 1", is_daily_exhaustion=True)
        elif model_id == "gemini-3.1-flash-lite":
            return f"Successful response generated from {model_id}"
        raise RuntimeError(f"Unexpected model called: {model_id}")

    mock_google_provider.generate_text.side_effect = fake_generate
    gateway._providers["google"] = mock_google_provider

    models_fixture = [
        {
            "id": 1,
            "provider": "google",
            "model_id": "gemini-3.5-flash-lite",
            "api_key_alias": "GEMINI_API_KEY",
            "priority": 1,
            "rpm_limit": 15,
            "rpd_limit": 500,
            "calls_today": 0,
            "is_active": True,
        },
        {
            "id": 2,
            "provider": "google",
            "model_id": "gemini-3.1-flash-lite",
            "api_key_alias": "GEMINI_API_KEY",
            "priority": 2,
            "rpm_limit": 15,
            "rpd_limit": 500,
            "calls_today": 0,
            "is_active": True,
        },
    ]

    with patch.object(gateway, "get_registered_models", return_value=models_fixture), \
         patch.object(gateway, "_increment_db_usage"), \
         patch.object(gateway, "update_model_used_calls"):
        # Execute generation
        result = gateway.execute_generation("Evaluate this job matching profile.")

    # Verification:
    # 1. Switched to Model 2 (gemini-3.1-flash-lite)
    assert result["model_id"] == "gemini-3.1-flash-lite"
    assert result["priority"] == 2
    assert "Successful response generated from gemini-3.1-flash-lite" in result["text"]
    assert result["provider"] == "google"

    # 2. Both models were contacted in order (Model 1 failed, Model 2 succeeded)
    assert mock_google_provider.generate_text.call_count == 2
    called_models = [call.kwargs.get("model_id") or call.args[0] for call in mock_google_provider.generate_text.call_args_list]
    assert called_models[0] == "gemini-3.5-flash-lite"
    assert called_models[1] == "gemini-3.1-flash-lite"


def test_ai_gateway_all_models_exhausted_raises_exception(mock_env_manager):
    """
    When every model in the cascade fails or hits quota, AllModelsExhaustedError is raised.
    """
    gateway = AIGateway(env_manager=mock_env_manager)

    mock_provider = MagicMock()
    mock_provider.generate_text.side_effect = ProviderRateLimitError("Quota limit hit", is_daily_exhaustion=True)
    gateway._providers["google"] = mock_provider

    models_fixture = [
        {
            "id": 1,
            "provider": "google",
            "model_id": "gemini-3.5-flash-lite",
            "api_key_alias": "GEMINI_API_KEY",
            "priority": 1,
            "rpm_limit": 15,
            "rpd_limit": 500,
            "calls_today": 0,
            "is_active": True,
        }
    ]

    with patch.object(gateway, "get_registered_models", return_value=models_fixture), \
         patch.object(gateway, "_increment_db_usage"), \
         patch.object(gateway, "update_model_used_calls"):
        with pytest.raises(AllModelsExhaustedError):
            gateway.execute_generation("Test prompt when all models fail.")


def test_ai_gateway_404_not_found_quarantine_30_minutes(mock_env_manager):
    """
    Verifies that when a model returns HTTP 404 (ProviderNotFoundError),
    it is placed into a 30-minute (1800-second) quarantine and the gateway
    cascades to the next model in the waterfall.
    """
    gateway = AIGateway(env_manager=mock_env_manager)

    mock_provider = MagicMock()

    def fake_generate(model_id, prompt, api_key, timeout_seconds=15.0):
        if model_id == "gemini-legacy-model":
            raise ProviderNotFoundError("Model not found 404")
        elif model_id == "gemini-fallback-model":
            return "Generated by fallback model"
        raise RuntimeError("Unexpected model called")

    mock_provider.generate_text.side_effect = fake_generate
    gateway._providers["google"] = mock_provider

    models_fixture = [
        {
            "id": 1,
            "provider": "google",
            "model_id": "gemini-legacy-model",
            "api_key_alias": "GEMINI_API_KEY",
            "priority": 1,
            "rpm_limit": 15,
            "rpd_limit": 500,
            "calls_today": 0,
            "is_active": True,
        },
        {
            "id": 2,
            "provider": "google",
            "model_id": "gemini-fallback-model",
            "api_key_alias": "GEMINI_API_KEY",
            "priority": 2,
            "rpm_limit": 15,
            "rpd_limit": 500,
            "calls_today": 0,
            "is_active": True,
        },
    ]

    with patch.object(gateway, "get_registered_models", return_value=models_fixture), \
         patch.object(gateway, "_increment_db_usage"):
        res = gateway.execute_generation("Test prompt")
        assert res["text"] == "Generated by fallback model"
        assert res["model_id"] == "gemini-fallback-model"

        # Verify that gemini-legacy-model is in cooldown (cooling down for 30 minutes)
        assert gateway._is_cooling_down("gemini-legacy-model", "GEMINI_API_KEY") is True
        # Check cooldown expiry time is around 1800 seconds in the future
        cooldown_epoch = gateway._cooldowns[("gemini-legacy-model", "GEMINI_API_KEY")]
        import time
        remaining_seconds = cooldown_epoch - time.time()
        assert 1700 <= remaining_seconds <= 1800

