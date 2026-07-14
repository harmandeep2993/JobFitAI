# llm/providers/openai.py
"""
OpenAI provider adapter used by llm/router.py.

Exposes check() and call() following the same interface as groq.py so
the router can switch providers without knowing their internals.
Do not call this module directly from routes or services.
"""

import os
import time

import requests

from core.config import (
    LLM_MAX_OUTPUT_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
    OPENAI_CONFIG,
)
from core.logger import get_logger

logger = get_logger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Safe to load at module level - config.py import above ensures
# load_dotenv() has already run before this line executes
_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
_MODEL = OPENAI_CONFIG.get("model", "gpt-4o-mini")


def has_key() -> bool:
    """Whether an API key is configured. Never exposes any part of the key."""
    return bool(_API_KEY)


def check() -> bool:
    """
    Check if OpenAI API key is configured and valid.

    Returns:
        bool: True if API key is set and reachable
    """
    if not _API_KEY:
        logger.warning("[OpenAI] No API key found - set OPENAI_API_KEY in .env")
        return False

    try:
        r = requests.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {_API_KEY}"},
            timeout=5,
        )
        return r.status_code == 200

    except Exception as e:
        logger.error("[OpenAI] Connectivity check failed: %s", e)
        return False


def call(prompt, model: str | None = None, json_mode: bool = True):
    """
    Send prompt to OpenAI and return response.

    Args:
        prompt (str): Prompt text
        model (str | None): Model id to use; falls back to the config default.
        json_mode (bool): When True, response_format forces strictly valid
            JSON output - no fences, no prose, no broken quoting.

    Returns:
        str: Response text or None if failed
    """
    if not _API_KEY:
        logger.warning("[OpenAI] No API key set found - set OPENAI_API_KEY in .env")
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
    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    }

    # Retry on transient rate-limit / server errors so a 429 under load
    # doesn't turn a real job into a false "JD unavailable".
    for attempt in range(4):
        try:
            response = requests.post(
                OPENAI_URL, headers=headers, json=payload, timeout=LLM_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                choice = data["choices"][0]
                finish_reason = choice.get("finish_reason", "unknown")
                content = choice["message"]["content"].strip()
                if finish_reason == "length":
                    logger.warning(
                        "[OpenAI] Response truncated at token limit (%d chars) - increase max_output_tokens",
                        len(content),
                    )
                return content

            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                wait = float(response.headers.get("retry-after", 2 * (attempt + 1)))
                logger.warning(
                    "[OpenAI] %s - retry in %.0fs", response.status_code, min(wait, 30)
                )
                time.sleep(min(wait, 30))
                continue

            logger.error(
                "[OpenAI] error %s: %s", response.status_code, response.text[:200]
            )
            return None

        except requests.RequestException as e:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            logger.error("[OpenAI] call() failed: %s", e)
            return None
