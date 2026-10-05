"""
Job Bucket — Multi-Model & Multi-Key Intelligent AI Gateway Engine
Orchestrates:
1. Micro-Level In-Memory Cache: Sliding-window RPM throttler with automatic recovery after 60-second cooldowns.
2. Macro-Level Database Quota Registry: Neon PostgreSQL table ai_model_registry tracking RPD and daily resets at UTC boundary.
3. Multi-Model Waterfall & Multi-Key Rotation: Cascades across models on same key, rotates across API keys.
4. Extensible Provider Architecture: Plugs in Google, Anthropic, OpenAI adapters.
5. Manual Quota Adjustments: Supports user-defined overrides of calls_today from the UI.
"""

import collections
import logging
import os
import time
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from src.db_config import DatabaseConfig, connect_database
from src.env_manager import EnvKeyManager
from src.providers.base import (
    BaseLLMProvider,
    ProviderRateLimitError,
    ProviderAuthError,
    ProviderServerError,
    ProviderBadRequestError,
    ProviderNotFoundError,
    ProviderError,
)
from src.providers.gemini_provider import GeminiProvider
from src.providers.openai_provider import OpenAIProvider
from src.providers.anthropic_provider import AnthropicProvider

logger = logging.getLogger(__name__)


class AllModelsExhaustedError(Exception):
    """Raised when every active model and key in the registry has exhausted its quota or is unavailable."""
    pass


class AIGateway:
    """
    Intelligent AI Gateway Router managing multi-model cascades, rate limiting, and quota failovers.
    """

    def __init__(
        self,
        db_config: Optional[Any] = None,
        env_manager: Optional[EnvKeyManager] = None,
    ):
        self.db_config = db_config or DatabaseConfig
        self.env_manager = env_manager or EnvKeyManager()

        # Provider adapter registry
        self._providers: Dict[str, BaseLLMProvider] = {
            "google": GeminiProvider(),
            "openai": OpenAIProvider(),
            "anthropic": AnthropicProvider(),
        }

        # Micro-level in-memory cache:
        # Key: (model_id, api_key_alias) -> collections.deque of timestamps within rolling 60s
        self._rpm_window: Dict[Tuple[str, str], collections.deque] = collections.defaultdict(collections.deque)

        # In-memory cooldowns: Key: (model_id, api_key_alias) -> epoch timestamp until cooldown expires
        self._cooldowns: Dict[Tuple[str, str], float] = {}

    def get_connection(self):
        """Creates psycopg2 connection to Neon PostgreSQL."""
        if hasattr(self.db_config, "get_connection_dict"):
            cfg = self.db_config.get_connection_dict()
        elif isinstance(self.db_config, dict):
            cfg = self.db_config
        else:
            cfg = DatabaseConfig.get_connection_dict()
        return connect_database(cfg)

    # -------------------------------------------------------------------------
    # Database CRUD & Quota Synchronization
    # -------------------------------------------------------------------------

    def get_registered_models(self, active_only: bool = False) -> List[Dict[str, Any]]:
        """
        Retrieves models from PostgreSQL. Automatically resets calls_today if a new calendar day has started.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # 1. Reset quotas if date changed (00:00 UTC)
                    cur.execute(
                        """
                        UPDATE ai_model_registry
                        SET calls_today = 0,
                            tokens_today = 0,
                            last_reset_date = CURRENT_DATE,
                            status_note = 'Daily quota refreshed'
                        WHERE last_reset_date < CURRENT_DATE;
                        """
                    )
                    conn.commit()

                    # 2. Query models
                    query = """
                        SELECT id, provider, model_id, api_key_alias, rpm_limit, tpm_limit,
                               rpd_limit, calls_today, tokens_today, last_reset_date,
                               last_called_at, is_active, priority, cooldown_until, status_note
                        FROM ai_model_registry
                    """
                    if active_only:
                        query += " WHERE is_active = TRUE"
                    query += " ORDER BY priority ASC, id ASC;"

                    cur.execute(query)
                    rows = cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Error fetching AI model registry from database: {e}")
            return []
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def add_model(
        self,
        provider: str,
        model_id: str,
        api_key_alias: str = "GEMINI_API_KEY",
        rpm_limit: int = 15,
        tpm_limit: int = 250000,
        rpd_limit: int = 500,
        priority: int = 1,
    ) -> bool:
        """Adds a new model configuration into the registry."""
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO ai_model_registry
                            (provider, model_id, api_key_alias, rpm_limit, tpm_limit, rpd_limit, priority, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                        ON CONFLICT (provider, model_id, api_key_alias) DO UPDATE
                        SET rpm_limit = EXCLUDED.rpm_limit,
                            tpm_limit = EXCLUDED.tpm_limit,
                            rpd_limit = EXCLUDED.rpd_limit,
                            priority = EXCLUDED.priority,
                            is_active = TRUE;
                        """,
                        (provider.lower().strip(), model_id.strip(), api_key_alias.strip().upper(),
                         rpm_limit, tpm_limit, rpd_limit, priority)
                    )
                    conn.commit()
            return True
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def update_model(
        self,
        model_id: str,
        api_key_alias: str,
        rpm_limit: int,
        rpd_limit: int,
        priority: int,
        is_active: bool,
    ) -> bool:
        """Updates parameters for an existing model configuration."""
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE ai_model_registry
                        SET rpm_limit = %s,
                            rpd_limit = %s,
                            priority = %s,
                            is_active = %s,
                            updated_at = NOW()
                        WHERE model_id = %s AND api_key_alias = %s;
                        """,
                        (rpm_limit, rpd_limit, priority, is_active, model_id, api_key_alias)
                    )
                    conn.commit()
            return True
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def update_model_used_calls(
        self,
        model_id: str,
        api_key_alias: str,
        calls_today: int,
    ) -> bool:
        """
        Allows user to override or synchronize today's consumed calls (e.g. if key was used outside job-bucket).
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE ai_model_registry
                        SET calls_today = %s,
                            updated_at = NOW(),
                            status_note = %s
                        WHERE model_id = %s AND api_key_alias = %s;
                        """,
                        (max(0, calls_today), f"Manual RPD update to {calls_today}", model_id, api_key_alias)
                    )
                    conn.commit()
            return True
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def toggle_model_active(self, model_id: str, api_key_alias: str, is_active: bool) -> bool:
        """Enables or disables a model."""
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE ai_model_registry
                        SET is_active = %s, updated_at = NOW()
                        WHERE model_id = %s AND api_key_alias = %s;
                        """,
                        (is_active, model_id, api_key_alias)
                    )
                    conn.commit()
            return True
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def delete_model(self, model_row_id: int) -> bool:
        """Deletes a model entry by ID from ai_model_registry."""
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM ai_model_registry WHERE id = %s;", (model_row_id,))
                    conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to delete model {model_row_id}: {e}")
            return False
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def reorder_models(self, ordered_model_ids: List[int]) -> bool:
        """
        Atomically updates the priority of models to match the order in ordered_model_ids (1-indexed).
        Guarantees strict, contiguous integer priorities with zero collisions.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    for index, model_id in enumerate(ordered_model_ids, start=1):
                        cur.execute(
                            """
                            UPDATE ai_model_registry
                            SET priority = %s,
                                updated_at = NOW()
                            WHERE id = %s;
                            """,
                            (index, model_id)
                        )
                    conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to reorder models: {e}")
            return False
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # Micro-Level In-Memory Cache (Sliding Window & Cooldowns)
    # -------------------------------------------------------------------------

    def _is_rpm_available(self, model_id: str, key_alias: str, rpm_limit: int) -> bool:
        """
        Checks rolling 60-second window in memory.
        Prunes timestamps older than 60s. Returns True if requests in last minute < rpm_limit.
        """
        now = time.time()
        window = self._rpm_window[(model_id, key_alias)]
        while window and now - window[0] > 60.0:
            window.popleft()
        return len(window) < rpm_limit

    def _record_call_memory(self, model_id: str, key_alias: str):
        """Records timestamp of an outgoing request in the rolling 60-second window."""
        now = time.time()
        window = self._rpm_window[(model_id, key_alias)]
        window.append(now)

    def _is_cooling_down(self, model_id: str, key_alias: str) -> bool:
        """
        Checks whether model is currently in temporary cooldown.
        Automatically recovers once cooldown epoch passes!
        """
        now = time.time()
        cooldown = self._cooldowns.get((model_id, key_alias))
        if cooldown and now < cooldown:
            return True
        elif cooldown and now >= cooldown:
            # Cooldown expired! Auto-recover model back to active rotation
            del self._cooldowns[(model_id, key_alias)]
            return False
        return False

    def trip_cooldown(self, model_id: str, key_alias: str, duration_seconds: float = 60.0):
        """Sets an in-memory temporary cooldown on a model."""
        self._cooldowns[(model_id, key_alias)] = time.time() + duration_seconds

    # -------------------------------------------------------------------------
    # Intelligent Waterfall Execution
    # -------------------------------------------------------------------------

    def execute_generation(
        self,
        prompt: str,
        timeout_seconds: float = 15.0,
    ) -> Dict[str, Any]:
        """
        Executes text generation using the cascading model waterfall and key rotation.
        Returns:
            {
                "text": str,
                "model_id": str,
                "provider": str,
                "api_key_alias": str,
                "priority": int,
            }
        Raises AllModelsExhaustedError if no model could service the request.
        """
        models = self.get_registered_models(active_only=True)
        if not models:
            raise AllModelsExhaustedError("No active AI models registered in database.")

        errors_encountered = []

        for m in models:
            m_id = m["model_id"]
            provider_name = m["provider"].lower()
            key_alias = m["api_key_alias"]
            rpd_limit = m.get("rpd_limit", 500)
            rpm_limit = m.get("rpm_limit", 15)
            calls_today = m.get("calls_today", 0)

            # 1. Check RPD (Daily Limit)
            if calls_today >= rpd_limit:
                logger.info(f"Model {m_id} ({key_alias}) has reached daily limit ({calls_today}/{rpd_limit}). Skipping.")
                continue

            # 2. Check temporary cooldown
            if self._is_cooling_down(m_id, key_alias):
                logger.info(f"Model {m_id} ({key_alias}) is cooling down from recent 429. Skipping.")
                continue

            # 3. Check rolling 60-second RPM window
            if not self._is_rpm_available(m_id, key_alias, rpm_limit):
                logger.warning(f"Model {m_id} ({key_alias}) hit rolling RPM limit ({rpm_limit}/min). Temporary 15s pause.")
                self.trip_cooldown(m_id, key_alias, duration_seconds=15.0)
                continue

            # 4. Resolve API key
            api_key = self.env_manager.get_key(key_alias)
            if not api_key:
                logger.warning(f"No API key configured for alias '{key_alias}'. Skipping {m_id}.")
                continue

            # 5. Resolve provider adapter
            provider = self._providers.get(provider_name)
            if not provider:
                logger.warning(f"Unsupported provider '{provider_name}' for model {m_id}. Skipping.")
                continue

            # 6. Execute generation call
            try:
                logger.info(f"Routing request to model: {m_id} (Priority {m['priority']}, Key: {key_alias})")
                text_response = provider.generate_text(
                    model_id=m_id,
                    prompt=prompt,
                    api_key=api_key,
                    timeout_seconds=timeout_seconds,
                )

                # Record successful call
                self._record_call_memory(m_id, key_alias)
                self._increment_db_usage(m["id"])

                return {
                    "text": text_response,
                    "model_id": m_id,
                    "provider": provider_name,
                    "api_key_alias": key_alias,
                    "priority": m["priority"],
                }

            except ProviderRateLimitError as rle:
                # HTTP 429 encountered
                if rle.is_daily_exhaustion:
                    logger.warning(f"Model {m_id} daily quota exhausted. Tripping for rest of day.")
                    self.update_model_used_calls(m_id, key_alias, rpd_limit)
                else:
                    logger.warning(f"Model {m_id} per-minute rate limit hit. Cooling down for 60s.")
                    self.trip_cooldown(m_id, key_alias, duration_seconds=60.0)

                errors_encountered.append(f"{m_id}: 429 RateLimit ({rle})")
                continue

            except ProviderAuthError as pae:
                logger.error(f"Auth error for {m_id} on key {key_alias}: {pae}. Tripping cooldown.")
                self.trip_cooldown(m_id, key_alias, duration_seconds=3600.0)
                errors_encountered.append(f"{m_id}: AuthError ({pae})")
                continue

            except ProviderNotFoundError as pnfe:
                logger.error(f"Model {m_id} not found / deprecated (404): {pnfe}. Quarantining for 30 minutes.")
                self.trip_cooldown(m_id, key_alias, duration_seconds=1800.0)  # 30-minute quarantine
                errors_encountered.append(f"{m_id}: NotFound ({pnfe})")
                continue

            except ProviderServerError as pse:
                logger.warning(f"Server error for {m_id}: {pse}. Cascading to next model.")
                self.trip_cooldown(m_id, key_alias, duration_seconds=10.0)
                errors_encountered.append(f"{m_id}: ServerError ({pse})")
                continue

            except Exception as e:
                logger.warning(f"Unexpected error calling {m_id}: {e}. Cascading to next model.")
                errors_encountered.append(f"{m_id}: {e}")
                continue

        # If loop finishes without returning, all active models failed or are exhausted
        raise AllModelsExhaustedError(
            f"All registered models and keys exhausted or unreachable. Errors: {'; '.join(errors_encountered)}"
        )

    def _increment_db_usage(self, model_row_id: int):
        """Atomically increments calls_today in PostgreSQL."""
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE ai_model_registry
                        SET calls_today = calls_today + 1,
                            last_called_at = NOW(),
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (model_row_id,)
                    )
                    conn.commit()
        except Exception as e:
            logger.error(f"Failed to increment database calls_today for model {model_row_id}: {e}")
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass
