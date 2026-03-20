"""
Platform AI client — routes all AI requests to shared-ai-platform.

Architecture rule: avantika-secretary-ai NEVER calls Anthropic/OpenAI/Ollama directly.
All AI traffic goes through AI_PLATFORM_URL. The platform decides which model to use
(Ollama → cheap model → premium fallback) based on its own routing logic.

Request format (POST AI_PLATFORM_URL):
    {
        "messages": [{"role": "user", "content": "..."}],
        "system":   "optional system prompt",
        "max_tokens": 2048
    }

Auth header: X-Api-Key: <AI_APP_KEY>

Response: JSON with one of: reply / response / content / text / answer / message
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("secretaryai.platform_ai")

_PLATFORM_URL: str = os.getenv("AI_PLATFORM_URL", "").rstrip("/")
_APP_KEY: str = os.getenv("AI_APP_KEY", "")
_TIMEOUT: int = int(os.getenv("AI_TIMEOUT_SECS", "60"))
_MAX_TOKENS: int = int(os.getenv("AI_MAX_TOKENS", "2048"))

# Candidate field names used by different platform implementations
_REPLY_FIELDS = ("reply", "response", "content", "text", "answer", "message", "output")


def is_configured() -> bool:
    """Return True if the platform URL has been set."""
    return bool(_PLATFORM_URL)


def _build_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if _APP_KEY:
        headers["X-Api-Key"] = _APP_KEY
    else:
        logger.warning("AI_APP_KEY is not set — platform may reject the request (401)")
    return headers


def _extract_reply(data: Any) -> str:
    """Extract reply text from various platform response formats."""
    if isinstance(data, str):
        return data.strip()
    if isinstance(data, dict):
        for field in _REPLY_FIELDS:
            val = data.get(field)
            if val:
                if isinstance(val, list):
                    # Anthropic-style content blocks: [{"type": "text", "text": "..."}]
                    return " ".join(
                        block.get("text", "") for block in val
                        if isinstance(block, dict)
                    ).strip()
                return str(val).strip()
        logger.warning("No recognised reply field in response. Keys: %s", list(data.keys()))
        return str(data)
    return str(data)


def call(
    messages: list[dict[str, str]],
    system: str = "",
    max_tokens: int | None = None,
) -> str:
    """
    Send a chat request to shared-ai-platform and return reply text.
    Raises RuntimeError with a user-friendly message on any failure.
    """
    if not _PLATFORM_URL:
        raise RuntimeError(
            "AI_PLATFORM_URL is not set. "
            "Add it to Azure App Settings: AI_PLATFORM_URL=https://your-platform.azurewebsites.net/api/chat"
        )

    payload: dict[str, Any] = {
        "messages": messages,
        "max_tokens": max_tokens or _MAX_TOKENS,
    }
    if system:
        payload["system"] = system

    logger.info(
        "AI request | url=%s | key_present=%s | messages=%d | max_tokens=%d",
        _PLATFORM_URL, bool(_APP_KEY), len(messages), payload["max_tokens"],
    )

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = client.post(_PLATFORM_URL, json=payload, headers=_build_headers())

        logger.info("AI response | status=%d | bytes=%d", resp.status_code, len(resp.content))

        if resp.status_code == 401:
            logger.error("AI 401 Unauthorized | url=%s | key_present=%s", _PLATFORM_URL, bool(_APP_KEY))
            raise RuntimeError(
                "AI platform rejected the request (401 Unauthorized). "
                "Verify AI_APP_KEY matches AI_INTERNAL_APP_KEYS in shared-ai-platform."
            )
        if resp.status_code == 403:
            raise RuntimeError(
                "AI platform denied access (403 Forbidden). "
                "Check that AI_APP_KEY is valid and AI_REQUIRE_APP_KEY is configured."
            )
        if resp.status_code == 429:
            logger.warning("AI 429 rate-limited | url=%s", _PLATFORM_URL)
            raise RuntimeError(
                "AI platform is rate-limited. Please try again in a moment."
            )
        if resp.status_code >= 500:
            logger.error("AI server error %d | body=%s", resp.status_code, resp.text[:300])
            raise RuntimeError(
                f"AI platform returned a server error ({resp.status_code}). "
                "Check shared-ai-platform logs."
            )
        resp.raise_for_status()

        data = resp.json()
        reply = _extract_reply(data)
        logger.info("AI reply extracted | len=%d", len(reply))
        return reply

    except httpx.TimeoutException:
        logger.error("AI timeout after %ds | url=%s", _TIMEOUT, _PLATFORM_URL)
        raise RuntimeError(
            f"AI platform did not respond within {_TIMEOUT}s. "
            "Check shared-ai-platform health and increase AI_TIMEOUT_SECS if needed."
        )
    except httpx.ConnectError as exc:
        logger.error("AI connect error | url=%s | %s", _PLATFORM_URL, exc)
        raise RuntimeError(
            "Cannot reach the AI platform. "
            "Verify AI_PLATFORM_URL is correct and shared-ai-platform is running."
        )
    except RuntimeError:
        raise
    except Exception as exc:
        logger.exception("Unexpected AI call failure: %s", exc)
        raise RuntimeError(f"AI call failed unexpectedly: {exc}") from exc


class PlatformAISession:
    """
    Stateful conversation session backed by the shared-ai-platform.
    Maintains history locally and sends full context on each request.
    Drop-in replacement for SecretaryAgent.
    """

    def __init__(self, system_prompt: str = "") -> None:
        self.system = system_prompt
        self.history: list[dict[str, str]] = []

    def clear_history(self) -> None:
        self.history.clear()

    def get_response(self, user_message: str) -> str:
        self.history.append({"role": "user", "content": user_message})
        try:
            reply = call(messages=list(self.history), system=self.system)
        except RuntimeError as exc:
            reply = f"⚠️ {exc}"
            logger.warning("AI fallback message returned to user: %s", reply[:120])
        self.history.append({"role": "assistant", "content": reply})
        return reply
