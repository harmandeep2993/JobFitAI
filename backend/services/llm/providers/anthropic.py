# services/llm/providers/anthropic.py
"""
Anthropic (Claude) provider.

Used for the quality tier - resume rewriting and the responsibility coverage
judge - where instruction-following on "never invent anything" matters more than
per-call cost.

The Messages API differs from the OpenAI chat API in three ways this module
hides from the rest of the app:
  - auth is x-api-key, not a bearer token, and needs an anthropic-version header
  - max_tokens is required, not optional
  - there is no response_format json_object; JSON is forced by prefilling the
    assistant turn with an opening brace (see call()).
"""

import os
import time

import requests

from core.config import (
    ANTHROPIC_CONFIG,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_TEMPERATURE,
    LLM_TIMEOUT,
)
from core.logger import get_logger

logger = get_logger(__name__)

ANTHROPIC_URL = ANTHROPIC_CONFIG.get("url", "https://api.anthropic.com/v1/messages")
_MODEL = ANTHROPIC_CONFIG.get("model", "claude-sonnet-5")
_API_VERSION = "2023-06-01"

_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# Retry the same transient failures the other providers retry.
_RETRY_STATUS = (429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 4


def has_key() -> bool:
    """Whether an API key is configured. Never exposes any part of the key."""
    return bool(_API_KEY)


def check() -> bool:
    """Return True when the API key is set and Anthropic is reachable."""
    if not _API_KEY:
        logger.warning("[Anthropic] No API key found - set ANTHROPIC_API_KEY in .env")
        return False
    try:
        # A 1-token call is the cheapest reliable liveness probe: Anthropic has
        # no unauthenticated models endpoint to GET.
        r = requests.post(
            ANTHROPIC_URL,
            headers=_headers(),
            json={
                "model": _MODEL,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        logger.error("[Anthropic] Connectivity check failed: %s", e)
        return False


def _headers() -> dict:
    return {
        "x-api-key": _API_KEY,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
    }


def call(prompt: str, model: str | None = None, json_mode: bool = True) -> str | None:
    """
    Send a prompt to Claude and return the response text.

    Args:
        prompt: Prompt text.
        model: Model id; falls back to the config default.
        json_mode: When True, the assistant turn is prefilled with "{" so the
            model can only continue a JSON object. The brace is prepended back
            onto the reply, since the API returns only the continuation.

    Returns:
        Response text, or None on failure (never raises - the router retries).
    """
    if not _API_KEY:
        logger.warning("[Anthropic] No API key found - set ANTHROPIC_API_KEY in .env")
        return None

    use_model = model or _MODEL
    messages = [{"role": "user", "content": prompt}]
    if json_mode:
        # Prefill: Claude must continue from an open brace, so it cannot emit a
        # preamble, a code fence, or prose around the JSON.
        messages.append({"role": "assistant", "content": "{"})

    payload = {
        "model": use_model,
        "max_tokens": LLM_MAX_OUTPUT_TOKENS,
        "temperature": LLM_TEMPERATURE,
        "messages": messages,
    }

    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = requests.post(
                ANTHROPIC_URL, headers=_headers(), json=payload, timeout=LLM_TIMEOUT
            )
            if response.status_code == 200:
                data = response.json()
                blocks = data.get("content") or []
                text = "".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                ).strip()
                if not text:
                    logger.warning("[Anthropic] Empty content block in response")
                    return None
                if data.get("stop_reason") == "max_tokens":
                    logger.warning(
                        "[Anthropic] Response hit the token limit (%d chars) - "
                        "increase max_output_tokens",
                        len(text),
                    )
                # Put back the brace we prefilled, so the caller sees valid JSON.
                return "{" + text if json_mode else text

            if response.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
                wait = min(2 * (attempt + 1), 30)
                logger.warning(
                    "[Anthropic] %s - retry in %ds", response.status_code, wait
                )
                time.sleep(wait)
                continue

            logger.error(
                "[Anthropic] Error %s: %s", response.status_code, response.text[:200]
            )
            return None

        except Exception as e:
            logger.error("[Anthropic] call() failed: %s", e)
            return None

    return None
