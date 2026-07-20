"""Anthropic client wrapper.

Two things this centralises:

1. **Prompt caching.** The resume and profile are identical across every job in a
   scoring cycle, so they go in a cached prefix. This is the single biggest cost
   lever in the system — without it, a 2000-token resume is re-sent at full price
   for every posting scored.

2. **Structured output.** Scoring and tailoring both need parseable JSON. Models
   sometimes wrap JSON in prose or fences, so `complete_json` extracts and
   validates rather than trusting `json.loads` on the raw text.

If no API key is configured, `available` is False and callers fall back to
local-only behaviour instead of crashing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

# Retry only transient failures. A 400 means a malformed request, which retrying
# cannot fix.
_RETRYABLE = (429, 500, 502, 503, 504, 529)
MAX_RETRIES = 3


@dataclass(slots=True)
class LlmResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    stop_reason: str = ""

    @property
    def truncated(self) -> bool:
        """True when the model hit the token ceiling mid-answer.

        Worth checking explicitly: a truncated response usually yields invalid
        JSON, and the useful fix is a larger max_tokens, not a retry.
        """
        return self.stop_reason == "max_tokens"


class LlmClient:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._client: Any | None = None

    @property
    def available(self) -> bool:
        return bool(self._settings.anthropic_api_key)

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "anthropic package not installed; run pip install -r requirements.txt"
                ) from exc
            self._client = AsyncAnthropic(api_key=self._settings.anthropic_api_key)
        return self._client

    async def complete(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> LlmResult:
        if not self.available:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        import anthropic

        client = self._get_client()
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.messages.create(
                    model=model,
                    system=system,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                break
            except anthropic.APIStatusError as exc:
                if exc.status_code not in _RETRYABLE or attempt == MAX_RETRIES:
                    raise
                last_exc = exc
                delay = 2 ** attempt
                log.warning("anthropic %s; retry %d/%d in %ds",
                            exc.status_code, attempt, MAX_RETRIES, delay)
                await _sleep(delay)
            except anthropic.APIConnectionError as exc:
                if attempt == MAX_RETRIES:
                    raise
                last_exc = exc
                await _sleep(2 ** attempt)
        else:  # pragma: no cover
            raise last_exc or RuntimeError("anthropic call failed")

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        usage = resp.usage
        return LlmResult(
            text=text,
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            stop_reason=getattr(resp, "stop_reason", "") or "",
        )

    async def complete_json(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> tuple[dict, LlmResult]:
        """Complete and parse a JSON object, retrying once on unparseable output."""
        result = await self.complete(
            model=model, system=system, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
        )
        parsed = extract_json(result.text)
        if parsed is None:
            if result.truncated:
                raise ValueError(
                    f"{model} response hit max_tokens ({max_tokens}) and was cut off; "
                    "raise max_tokens for this call"
                )
            log.warning("%s returned unparseable JSON; retrying once", model)
            retry_messages = [
                *messages,
                {"role": "assistant", "content": result.text[:1500]},
                {"role": "user", "content":
                    "That was not valid JSON. Reply with the JSON object only — "
                    "no prose, no markdown fences."},
            ]
            result = await self.complete(
                model=model, system=system, messages=retry_messages,
                max_tokens=max_tokens, temperature=temperature,
            )
            parsed = extract_json(result.text)
            if parsed is None:
                raise ValueError(f"{model} did not return valid JSON: {result.text[:200]!r}")
        return parsed, result


def cached_system(static_prefix: str, dynamic_suffix: str = "") -> list[dict]:
    """Build a system prompt whose stable prefix is cached across calls.

    `static_prefix` must be byte-identical between calls or the cache misses —
    never interpolate per-job details into it.
    """
    blocks: list[dict] = [{
        "type": "text",
        "text": static_prefix,
        "cache_control": {"type": "ephemeral"},
    }]
    if dynamic_suffix:
        blocks.append({"type": "text", "text": dynamic_suffix})
    return blocks


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict | None:
    """Best-effort extraction of one JSON object from a model response."""
    if not text:
        return None
    candidates: list[str] = []

    if fenced := _FENCE.search(text):
        candidates.append(fenced.group(1))
    candidates.append(text)

    stripped = text.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start:end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


_client: LlmClient | None = None


def get_llm() -> LlmClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = LlmClient()
    return _client
