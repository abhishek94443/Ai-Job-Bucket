"""
Job Bucket — Environment Variable & API Key Manager
Provides atomic, safe reading and editing of provider API keys in .env
Guarantees:
- Never corrupts or removes other environment variables (e.g. DB credentials, ports).
- Immediate in-memory synchronization via os.environ so UI edits take effect instantly without restarting.
- Safe key masking for executive UI display (e.g. AIzaSy...9d2X).
"""

import os
import re
import tempfile
from typing import Dict, Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPPORTED_KEY_ALIASES = [
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_1",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
]


class EnvKeyManager:
    """
    Manages API keys stored in .env and os.environ.
    """

    def __init__(self, env_path: str = ".env"):
        self.env_path = env_path

    def get_key(self, alias: str) -> Optional[str]:
        """
        Retrieves API key value by alias strictly from os.environ.
        """
        val = os.getenv(alias)
        if val and val.strip():
            return val.strip()
        return None

    def mask_key(self, key_val: Optional[str]) -> str:
        """Returns safe status without exposing secret token characters."""
        if not key_val or len(key_val) < 8:
            return "Not configured"
        return "Secret stored in .env"

    def get_keys_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns a dictionary of all recognized key aliases, whether they are set, and masked values.
        """
        results = {}
        for alias in SUPPORTED_KEY_ALIASES:
            val = self.get_key(alias)
            results[alias] = {
                "alias": alias,
                "is_configured": bool(val),
                "masked": self.mask_key(val) if val else "Not configured",
                "length": len(val) if val else 0
            }
        return results

    def save_key(self, alias: str, new_value: str) -> bool:
        """
        Atomically writes or updates the key in .env and os.environ.
        """
        alias_clean = alias.strip().upper()
        val_clean = new_value.strip()

        if not alias_clean:
            raise ValueError("API Key alias cannot be empty.")

        # 1. Update live process memory
        os.environ[alias_clean] = val_clean

        # 2. Update .env file atomically
        lines = []
        key_found = False

        if os.path.exists(self.env_path):
            with open(self.env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        new_lines = []
        pattern = re.compile(rf"^\s*{re.escape(alias_clean)}\s*=")

        for line in lines:
            if pattern.match(line):
                new_lines.append(f"{alias_clean}={val_clean}\n")
                key_found = True
            else:
                new_lines.append(line)

        if not key_found:
            # Append if not present
            if new_lines and not new_lines[-1].endswith("\n"):
                new_lines[-1] += "\n"
            new_lines.append(f"{alias_clean}={val_clean}\n")

        # Atomic file write via tempfile
        dirname = os.path.dirname(os.path.abspath(self.env_path))
        with tempfile.NamedTemporaryFile("w", dir=dirname, delete=False, encoding="utf-8") as tf:
            tf.writelines(new_lines)
            temp_name = tf.name

        os.replace(temp_name, self.env_path)
        return True
