"""LLM client wrapper.

The active backend is **Google Gemini**, called through its REST API with the
already-installed `httpx` client. The Anthropic implementation using the
`anthropic` SDK is retained below, commented out, so switching back is a small
edit (`complete()` dispatch + restoring `_complete_anthropic`) rather than a
rewrite.

Two things this centralises:

1. **Structured output.** Scoring and tailoring both need parseable JSON. Models
   sometimes wrap JSON in prose or fences, so `complete_json` extracts and
   validates rather than trusting `json.loads` on the raw text.

2. **Shared context.** The resume and profile are identical across every job in a
   scoring cycle, so they are placed in the system instruction. With Gemini this
   context is reused at no extra cost per call; Claude needed explicit
   `cache_control` blocks (see the commented-out code).

If no API key is configured, `available` is False and callers fall back to
local-only behaviour instead of crashing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Retry only transient failures. A 4xx means a malformed request or a bad key,
# which retrying cannot fix.
_RETRYABLE = (429, 500, 502, 503, 504)
MAX_RETRIES = 3
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
# Long generations (a tailored resume is up to 4096 tokens) need headroom.
GEMINI_TIMEOUT = 120.0


class GeminiError(RuntimeError):
    """Non-transient Gemini API failure (bad key, blocked prompt, invalid model)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Gemini HTTP {status}: {detail}")
        self.status = status


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

    @property
    def available(self) -> bool:
        return bool(self._settings.gemini_api_key or self._settings.anthropic_api_key)

    @property
    def backend(self) -> str:
        if self._settings.gemini_api_key:
            return "gemini"
        if self._settings.anthropic_api_key:
            return "anthropic"
        return ""

    @property
    def triage_model(self) -> str:
        """Model used for tier-2 scoring. Backend-appropriate."""
        if self.backend == "gemini":
            return self._settings.gemini_model
        return self._settings.model_triage

    @property
    def tailor_model(self) -> str:
        """Model used for CV tailoring, cover letters and interview prep."""
        if self.backend == "gemini":
            return self._settings.gemini_tailor_model
        return self._settings.model_tailor

    async def complete(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.0,
        json_mode: bool = False,
    ) -> LlmResult:
        if not self.available:
            raise RuntimeError(
                "no LLM API key configured (GEMINI_API_KEY or ANTHROPIC_API_KEY)"
            )
        if self.backend == "gemini":
            return await self._complete_gemini(
                model=model, system=system, messages=messages,
                max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
            )
        # ================================================================
        # Anthropic backend. Disabled while Gemini is active; to restore,
        # uncomment `_complete_anthropic` / `_get_anthropic_client` below and
        # call `self._complete_anthropic(...)` here.
        # ================================================================
        raise RuntimeError(
            "ANTHROPIC_API_KEY is set but the Anthropic backend is disabled; "
            "add GEMINI_API_KEY to .env, or restore the commented-out "
            "Anthropic code in app/llm/client.py"
        )

    # ---------------------------------------------------------------- gemini

    async def _complete_gemini(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LlmResult:
        payload: dict[str, Any] = {
            "contents": [_gemini_content(m) for m in messages],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text := _flatten_system(system):
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if json_mode:
            # Ask the API to emit a JSON object directly, which is far more
            # reliable than fishing it out of prose.
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = f"{GEMINI_API}/models/{model}:generateContent"
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=GEMINI_TIMEOUT, follow_redirects=True
                ) as client:
                    resp = await client.post(
                        url,
                        params={"key": self._settings.gemini_api_key},
                        headers={"Content-Type": "application/json"},
                        json=payload,
                    )
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise
                last_exc = exc
                log.warning("gemini network error; retry %d/%d in %ds",
                            attempt, MAX_RETRIES, 2 ** attempt)
                await _sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                return _parse_gemini_response(model, resp.json())
            if resp.status_code in _RETRYABLE:
                last_exc = GeminiError(resp.status_code, (resp.text or "")[:200])
                delay = 2 ** attempt
                log.warning("gemini %s; retry %d/%d in %ds",
                            resp.status_code, attempt, MAX_RETRIES, delay)
                await _sleep(delay)
                continue
            raise GeminiError(resp.status_code, (resp.text or "")[:300])

        assert last_exc is not None
        raise last_exc

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
            max_tokens=max_tokens, temperature=temperature, json_mode=True,
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
                max_tokens=max_tokens, temperature=temperature, json_mode=True,
            )
            parsed = extract_json(result.text)
            if parsed is None:
                raise ValueError(f"{model} did not return valid JSON: {result.text[:200]!r}")
        return parsed, result

    # ---------------------------------------------------------------- anthropic
    # Retained for the switch back. Uncomment `_get_anthropic_client` and
    # `_complete_anthropic`, and point `complete()` at `_complete_anthropic`.
    #
    # def _get_anthropic_client(self) -> Any:
    #     if self._anthropic_client is None:
    #         try:
    #             from anthropic import AsyncAnthropic
    #         except ImportError as exc:  # pragma: no cover
    #             raise RuntimeError(
    #                 "anthropic package not installed; run pip install -r requirements.txt"
    #             ) from exc
    #         self._anthropic_client = AsyncAnthropic(
    #             api_key=self._settings.anthropic_api_key
    #         )
    #     return self._anthropic_client
    #
    # async def _complete_anthropic(
    #     self,
    #     *,
    #     model: str,
    #     system: str | list[dict],
    #     messages: list[dict],
    #     max_tokens: int,
    #     temperature: float,
    # ) -> LlmResult:
    #     import anthropic
    #
    #     client = self._get_anthropic_client()
    #     last_exc: Exception | None = None
    #
    #     for attempt in range(1, MAX_RETRIES + 1):
    #         try:
    #             resp = await client.messages.create(
    #                 model=model,
    #                 system=system,
    #                 messages=messages,
    #                 max_tokens=max_tokens,
    #                 temperature=temperature,
    #             )
    #             break
    #         except anthropic.APIStatusError as exc:
    #             if exc.status_code not in _RETRYABLE or attempt == MAX_RETRIES:
    #                 raise
    #             last_exc = exc
    #             delay = 2 ** attempt
    #             log.warning("anthropic %s; retry %d/%d in %ds",
    #                         exc.status_code, attempt, MAX_RETRIES, delay)
    #             await _sleep(delay)
    #         except anthropic.APIConnectionError as exc:
    #             if attempt == MAX_RETRIES:
    #                 raise
    #             last_exc = exc
    #             await _sleep(2 ** attempt)
    #     else:  # pragma: no cover
    #         raise last_exc or RuntimeError("anthropic call failed")
    #
    #     text = "".join(
    #         block.text for block in resp.content if getattr(block, "type", "") == "text"
    #     )
    #     usage = resp.usage
    #     return LlmResult(
    #         text=text,
    #         model=model,
    #         input_tokens=getattr(usage, "input_tokens", 0) or 0,
    #         output_tokens=getattr(usage, "output_tokens", 0) or 0,
    #         cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
    #         cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    #         stop_reason=getattr(resp, "stop_reason", "") or "",
    #     )


def cached_system(static_prefix: str, dynamic_suffix: str = "") -> list[dict]:
    """Build a system prompt whose stable prefix is shared across calls.

    Gemini has no per-call caching switch, so the blocks here carry the metadata
    Claude's API used for explicit prompt caching; `_flatten_system` drops it
    when talking to Gemini. Keep the static prefix byte-identical between calls.
    """
    blocks: list[dict] = [{
        "type": "text",
        "text": static_prefix,
        "cache_control": {"type": "ephemeral"},
    }]
    if dynamic_suffix:
        blocks.append({"type": "text", "text": dynamic_suffix})
    return blocks


def _flatten_system(system: str | list[dict]) -> str:
    """Reduce the (possibly cached-block) system prompt to plain text."""
    if isinstance(system, str):
        return system
    return "\n".join(
        str(block.get("text") or "") for block in system if isinstance(block, dict)
    )


def _gemini_content(message: dict) -> dict:
    """Map the app's {role, content} messages onto Gemini's content shape."""
    role = str(message.get("role") or "user")
    if role == "assistant":
        role = "model"
    return {"role": role, "parts": [{"text": str(message.get("content") or "")}]}


def _parse_gemini_response(model: str, payload: object) -> LlmResult:
    """Turn a :generateContent response body into an LlmResult."""
    if not isinstance(payload, dict):
        raise GeminiError(200, "unexpected response payload")

    text = ""
    stop = ""
    candidates = payload.get("candidates") or []
    if candidates and isinstance(candidates[0], dict):
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or [] if isinstance(content, dict) else []
        text = "".join(
            str(p.get("text") or "") for p in parts if isinstance(p, dict)
        )
        stop = str(candidates[0].get("finishReason") or "")
        # Normalise so LlmResult.truncated behaves the same across backends.
        if stop == "MAX_TOKENS":
            stop = "max_tokens"
        elif stop:
            stop = stop.lower()

    usage = payload.get("usageMetadata") or {} if isinstance(payload, dict) else {}
    return LlmResult(
        text=text,
        model=model,
        input_tokens=int(usage.get("promptTokenCount") or 0),
        output_tokens=int(usage.get("candidatesTokenCount") or 0),
        cache_read_tokens=int(usage.get("cachedContentTokenCount") or 0),
        stop_reason=stop,
    )


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
