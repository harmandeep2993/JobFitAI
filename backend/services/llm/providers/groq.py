# llm/providers/groq.py
"""
Groq API provider for JobsFitAI.
Groq uses OpenAI-compatible chat completions API.
Get API key: https://console.groq.com
"""

import os

import requests

from core.config import (
    GROQ_CONFIG,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
)
from core.logger import get_logger

logger = get_logger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Safe to load at module level - config.py import above ensures
# load_dotenv() has already run before this line executes
_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
_MODEL = GROQ_CONFIG.get("model", "llama-3.1-8b-instant")


def has_key() -> bool:
    """Whether an API key is configured. Never exposes any part of the key."""
    return bool(_API_KEY)


def check() -> bool:
    """
    Check if Groq API key is configured and valid.
    Makes lightweight GET request to /v1/models endpoint.

    Returns:
        bool: True if API key is set and reachable
    """
    if not _API_KEY:
        logger.warning("[Groq] No API key found - set GROQ_API_KEY in .env")
        return False

    try:
        r = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {_API_KEY}"},
            timeout=5,
        )
        return r.status_code == 200

    except Exception as e:
        logger.error("[Groq] Connectivity check failed: %s", e)
        return False


def call(prompt: str, model: str | None = None, json_mode: bool = True) -> str | None:
    """
    Send prompt to Groq and return response text.

    Args:
        prompt (str): Prompt text
        model (str | None): Model id to use; falls back to the config default.
        json_mode (bool): When True, response_format forces strictly valid
            JSON output.

    Returns:
        str | None: Response text or None if failed
    """
    if not _API_KEY:
        logger.warning("[Groq] No API key found - set GROQ_API_KEY in .env")
        return None

    use_model = model or _MODEL

    payload = {
        "model": use_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": LLM_TEMPERATURE,
        "max_tokens": LLM_MAX_OUTPUT_TOKENS,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=LLM_TIMEOUT,
        )

        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()

        logger.error("[Groq] Error %s: %s", response.status_code, response.text[:200])
        return None

    except Exception as e:
        logger.error("[Groq] call() failed: %s", e)
        return None
