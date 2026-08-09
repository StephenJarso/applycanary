"""Multi-provider LLM client with automatic fallback chain.

Provider order (tried sequentially until one succeeds):
1. Gemini (primary) - needs GEMINI_API_KEY
2. OpenRouter (free models) - needs OPENROUTER_API_KEY
3. Groq (generous free tier) - needs GROQ_API_KEY
4. Ollama (local, unlimited) - needs OLLAMA_HOST

If no API key is configured for any provider, `available` is False and callers
fall back to local-only behaviour instead of crashing.
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
# Timeouts per provider
GEMINI_TIMEOUT = 120.0
OPENROUTER_TIMEOUT = 120.0
GROQ_TIMEOUT = 120.0
OLLAMA_TIMEOUT = 300.0  # Local models can be slower

# Provider API endpoints
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_API = "https://openrouter.ai/api/v1"
GROQ_API = "https://api.groq.com/openai/v1"


class ProviderError(RuntimeError):
    """Non-transient provider API failure."""

    def __init__(self, provider: str, status: int, detail: str) -> None:
        super().__init__(f"{provider} HTTP {status}: {detail}")
        self.provider = provider
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
        """True when the model hit the token ceiling mid-answer."""
        return self.stop_reason == "max_tokens"


class LlmClient:
    """Multi-provider LLM client with automatic fallback."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._provider_order = self._build_provider_order()

    def _build_provider_order(self) -> list[str]:
        """Build ordered list of available providers."""
        providers = []
        s = self._settings
        if s.gemini_api_key:
            providers.append("gemini")
        if s.openrouter_api_key:
            providers.append("openrouter")
        if s.groq_api_key:
            providers.append("groq")
        if s.ollama_host:
            providers.append("ollama")
        if s.anthropic_api_key:
            providers.append("anthropic")
        return providers

    @property
    def available(self) -> bool:
        return bool(self._provider_order)

    @property
    def active_provider(self) -> str:
        return self._provider_order[0] if self._provider_order else ""

    @property
    def triage_model(self) -> str:
        """Model used for tier-2 scoring. Backend-appropriate."""
        p = self.active_provider
        s = self._settings
        if p == "gemini":
            return s.gemini_model
        if p == "openrouter":
            return s.openrouter_triage_model
        if p == "groq":
            return s.groq_triage_model
        if p == "ollama":
            return s.ollama_triage_model
        if p == "anthropic":
            return s.model_triage
        return ""

    @property
    def tailor_model(self) -> str:
        """Model used for CV tailoring, cover letters and interview prep."""
        p = self.active_provider
        s = self._settings
        if p == "gemini":
            return s.gemini_tailor_model
        if p == "openrouter":
            return s.openrouter_tailor_model
        if p == "groq":
            return s.groq_tailor_model
        if p == "ollama":
            return s.ollama_tailor_model
        if p == "anthropic":
            return s.model_tailor
        return ""

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
        """Complete with automatic provider fallback."""
        if not self.available:
            raise RuntimeError(
                "no LLM provider configured. Set one of: "
                "GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, "
                "or run Ollama locally (OLLAMA_HOST)"
            )

        last_exc: Exception | None = None

        for provider in self._provider_order:
            try:
                log.debug("Trying provider: %s with model: %s", provider, model)
                if provider == "gemini":
                    return await self._complete_gemini(
                        model=model, system=system, messages=messages,
                        max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                    )
                if provider == "openrouter":
                    return await self._complete_openrouter(
                        model=model, system=system, messages=messages,
                        max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                    )
                if provider == "groq":
                    return await self._complete_groq(
                        model=model, system=system, messages=messages,
                        max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                    )
                if provider == "ollama":
                    return await self._complete_ollama(
                        model=model, system=system, messages=messages,
                        max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                    )
                if provider == "anthropic":
                    return await self._complete_anthropic(
                        model=model, system=system, messages=messages,
                        max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                    )
            except ProviderError as e:
                # Non-retryable error from this provider - try next
                log.warning("Provider %s failed: %s. Trying next...", provider, e)
                last_exc = e
                continue
            except Exception as e:
                # Unexpected error - try next provider
                log.warning("Provider %s error: %s. Trying next...", provider, e)
                last_exc = e
                continue

        # All providers exhausted
        raise RuntimeError(
            f"All {len(self._provider_order)} providers failed. Last error: {last_exc}"
        ) from last_exc

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
            payload["generationConfig"]["responseMimeType"] = "application/json"

        url = f"{GEMINI_API}/models/{model}:generateContent"
        return await self._post_with_retry(
            "gemini", url, model,
            params={"key": self._settings.gemini_api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
            parse_fn=_parse_gemini_response,
            timeout=GEMINI_TIMEOUT,
        )

    # ---------------------------------------------------------------- openrouter

    async def _complete_openrouter(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LlmResult:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": _flatten_system(system)}] + messages
            if _flatten_system(system) else messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{OPENROUTER_API}/chat/completions"
        return await self._post_with_retry(
            "openrouter", url, model,
            headers={
                "Authorization": f"Bearer {self._settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/applycanary",
                "X-Title": "ApplyCanary",
            },
            json=payload,
            parse_fn=_parse_openai_compatible_response,
            timeout=OPENROUTER_TIMEOUT,
        )

    # ---------------------------------------------------------------- groq

    async def _complete_groq(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LlmResult:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": _flatten_system(system)}] + messages
            if _flatten_system(system) else messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{GROQ_API}/chat/completions"
        return await self._post_with_retry(
            "groq", url, model,
            headers={
                "Authorization": f"Bearer {self._settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            parse_fn=_parse_openai_compatible_response,
            timeout=GROQ_TIMEOUT,
        )

    # ---------------------------------------------------------------- ollama

    async def _complete_ollama(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LlmResult:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": _flatten_system(system)}] + messages
            if _flatten_system(system) else messages,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"

        url = f"{self._settings.ollama_host.rstrip('/')}/api/chat"
        return await self._post_with_retry(
            "ollama", url, model,
            headers={"Content-Type": "application/json"},
            json=payload,
            parse_fn=_parse_ollama_response,
            timeout=OLLAMA_TIMEOUT,
        )

    # ---------------------------------------------------------------- anthropic (kept for compatibility)

    async def _complete_anthropic(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LlmResult:
        # Anthropic implementation kept for compatibility
        # Uncomment and install `anthropic` package if needed
        raise ProviderError("anthropic", 501, "Anthropic backend not implemented in multi-provider chain")

    # ---------------------------------------------------------------- shared retry logic

    async def _post_with_retry(
        self,
        provider: str,
        url: str,
        model: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        json: dict | None = None,
        parse_fn: Any,
        timeout: float,
    ) -> LlmResult:
        last_exc: Exception | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=True
                ) as client:
                    resp = await client.post(
                        url, params=params, headers=headers, json=json
                    )
            except httpx.RequestError as exc:
                if attempt == MAX_RETRIES:
                    raise
                last_exc = exc
                log.warning("%s network error; retry %d/%d in %ds",
                            provider, attempt, MAX_RETRIES, 2 ** attempt)
                await _sleep(2 ** attempt)
                continue

            if resp.status_code == 200:
                return parse_fn(model, resp.json())

            if resp.status_code in _RETRYABLE:
                last_exc = ProviderError(provider, resp.status_code, (resp.text or "")[:200])
                delay = 2 ** attempt
                log.warning("%s %s; retry %d/%d in %ds",
                            provider, resp.status_code, attempt, MAX_RETRIES, delay)
                await _sleep(delay)
                continue

            raise ProviderError(provider, resp.status_code, (resp.text or "")[:300])

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


# ---------------------------------------------------------------- response parsers

def _parse_gemini_response(model: str, payload: dict) -> LlmResult:
    if not isinstance(payload, dict):
        raise ProviderError("gemini", 200, "unexpected response payload")

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


def _parse_openai_compatible_response(model: str, payload: dict) -> LlmResult:
    """Parse OpenAI-compatible responses (OpenRouter, Groq)."""
    if not isinstance(payload, dict):
        raise ProviderError("openai-compat", 200, "unexpected response payload")

    text = ""
    stop = ""
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        text = str(msg.get("content") or "")
        stop = str(choices[0].get("finish_reason") or "")
        if stop == "length":
            stop = "max_tokens"

    usage = payload.get("usage") or {} if isinstance(payload, dict) else {}
    return LlmResult(
        text=text,
        model=model,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        stop_reason=stop,
    )


def _parse_ollama_response(model: str, payload: dict) -> LlmResult:
    """Parse Ollama /api/chat response."""
    if not isinstance(payload, dict):
        raise ProviderError("ollama", 200, "unexpected response payload")

    text = str(payload.get("message", {}).get("content") or "")
    stop = str(payload.get("done_reason") or "")
    if stop == "length":
        stop = "max_tokens"

    # Ollama doesn't return token counts in /api/chat
    return LlmResult(
        text=text,
        model=model,
        stop_reason=stop,
    )


# ---------------------------------------------------------------- utilities

def cached_system(static_prefix: str, dynamic_suffix: str = "") -> list[dict]:
    """Build a system prompt whose stable prefix is shared across calls."""
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
