"""Gemini AI agent — optional second opinion for morning recommendation.

Enabled when GOOGLE_AI_API_KEY is set in .env. Uses the same prompts as ClaudeAgent
but calls Google Gemini API via the google-genai SDK.
"""

import asyncio
import logging

from google import genai
from google.genai import types

from ai.prompts import get_system_prompt
from config import settings

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 30  # seconds; attempts wait 30s, 60s


def is_gemini_enabled() -> bool:
    """Check if Gemini is configured (GOOGLE_AI_API_KEY is set and non-empty)."""
    key = settings.GOOGLE_AI_API_KEY.get_secret_value()
    return bool(key and key.strip())


class GeminiAgent:
    def __init__(self) -> None:
        self.client = genai.Client(api_key=settings.GOOGLE_AI_API_KEY.get_secret_value())
        self.model = "gemini-2.5-flash"

    async def get_morning_recommendation(self, prompt: str) -> str:
        """Generate morning AI recommendation using Gemini.

        Retries up to 3 times with 30s/60s delays for transient errors (503 etc.).
        The google-genai SDK has its own short-interval retry; this adds a longer
        backoff on top for sustained high-demand periods.

        Args:
            prompt: The fully formatted MORNING_REPORT_PROMPT (same as sent to Claude).

        Returns:
            Gemini's recommendation text.

        Raises:
            Exception: if all retry attempts are exhausted.
        """
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                chunks: list[str] = []
                async for chunk in await self.client.aio.models.generate_content_stream(
                    model=self.model,
                    contents=prompt,
                    config={
                        "system_instruction": get_system_prompt(),
                        "max_output_tokens": 8192,
                        "thinking_config": types.ThinkingConfig(thinking_budget=4096),
                    },
                ):
                    if chunk.text:
                        chunks.append(chunk.text)
                    # Check for truncation on final chunk
                    if chunk.candidates:
                        finish = chunk.candidates[0].finish_reason
                        if finish and finish.name == "MAX_TOKENS":
                            logger.warning("Gemini response truncated (MAX_TOKENS)")
                return "".join(chunks)
            except Exception as exc:
                last_exc = exc
                logger.warning("Gemini attempt %d/%d failed: %s", attempt + 1, _MAX_RETRIES, exc)
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_BASE_DELAY * (attempt + 1)
                    logger.info("Retrying Gemini in %ds...", delay)
                    await asyncio.sleep(delay)

        logger.error("Gemini API call failed after %d attempts", _MAX_RETRIES, exc_info=last_exc)
        raise last_exc
