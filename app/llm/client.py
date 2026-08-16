"""Multi-provider LLM client with automatic fallback chain.

Provider order (tried sequentially until one succeeds):
1. xAI Grok - needs XAI_API_KEY (OpenAI-compatible; grok-4.6 by default)
2. Gemini (primary) - needs GEMINI_API_KEY
3. OpenRouter (free models) - needs OPENROUTER_API_KEY
4. Groq (generous free tier) - needs GROQ_API_KEY
5. Ollama (local, unlimited) - needs OLLAMA_HOST
6. Anthropic - needs ANTHROPIC_API_KEY
7. Amazon Bedrock - needs AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY

If no API key is configured for any provider, `available` is False and callers
fall back to local-only behaviour instead of crashing.

Every provider carries a per-provider cooldown (a simple circuit breaker):
after a provider fails it sits out for a while, so an exhausted quota, an
invalid key or a dead local model cannot make every pipeline call burn its
full retry budget. Without this, a broken provider chain turns the scheduler
into a retry storm that hammers SQLite ("database is locked") and stalls
login writes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Retry only transient failures. A 4xx means a malformed request or a bad key,
# which retrying cannot fix.
_RETRYABLE = (429, 500, 502, 503, 504)
MAX_RETRIES = 3
# Ceiling on a provider-supplied Retry-After. Beyond this, failing over to the
# next provider (or falling back to local scoring) beats blocking the pipeline.
MAX_RETRY_AFTER = 60.0
# Timeouts per provider
GEMINI_TIMEOUT = 120.0
OPENROUTER_TIMEOUT = 120.0
GROQ_TIMEOUT = 120.0
XAI_TIMEOUT = 120.0
OLLAMA_TIMEOUT = 300.0  # Local models can be slower

# Provider API endpoints
GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
OPENROUTER_API = "https://openrouter.ai/api/v1"
GROQ_API = "https://api.groq.com/openai/v1"
XAI_API = "https://api.x.ai/v1"


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
        # Circuit breaker state: provider -> monotonic() timestamp at which it
        # may be tried again. Initialised lazily on first failure.
        self._cooldowns: dict[str, float] = {}
        self._anthropic_client: Any | None = None

    def _build_provider_order(self) -> list[str]:
        """Build ordered list of available providers."""
        providers = []
        s = self._settings
        # xAI first: it is the provider the operator most recently activated
        # (and the one with working credits), so it should answer first. Every
        # provider behind it stays as a fallback; the circuit breaker keeps a
        # dead one from stalling the chain.
        if s.xai_api_key:
            providers.append("xai")
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
        if s.aws_access_key_id and s.aws_secret_access_key:
            providers.append("bedrock")
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
        if p == "xai":
            return s.xai_triage_model
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
        if p == "bedrock":
            return s.bedrock_model_id
        return ""

    @property
    def tailor_model(self) -> str:
        """Model used for CV tailoring, cover letters and interview prep."""
        p = self.active_provider
        s = self._settings
        if p == "xai":
            return s.xai_tailor_model
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
        if p == "bedrock":
            return s.bedrock_model_id
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
                "XAI_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY, "
                "GROQ_API_KEY, ANTHROPIC_API_KEY, AWS_ACCESS_KEY_ID (Bedrock), "
                "or run Ollama locally (OLLAMA_HOST)"
            )

        last_exc: Exception | None = None

        # Skip providers currently sitting out their cooldown. If every provider
        # is cooling down, fail fast rather than trying the soonest-recovering
        # one: with a dead provider chain that override would still burn retry
        # budget on every scheduler call, which is exactly what the breaker
        # exists to stop. Cooldowns expire on their own and the chain recovers.
        now = time.monotonic()
        candidates = [
            p for p in self._provider_order
            if now >= self._cooldowns.get(p, 0.0)
        ]
        if not candidates:
            cooling = ", ".join(
                f"{p} (~{int(self._cooldowns[p] - now)}s left)"
                for p in self._provider_order
            )
            raise RuntimeError(
                f"all LLM providers are cooling down after failures ({cooling}); "
                "retry once a cooldown expires"
            )

        for provider in candidates:
            try:
                log.debug("Trying provider: %s with model: %s", provider, model)
                result = await self._complete_for(
                    provider, model=model, system=system, messages=messages,
                    max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                )
                # Succeeded - lift any cooldown so a recovered provider is
                # used again immediately.
                self._cooldowns.pop(provider, None)
                return result
            except ProviderError as e:
                # Non-retryable error from this provider - try next, and sit the
                # provider out so it cannot burn the retry budget again soon.
                cooldown = self._cooldown_seconds(provider, e.status)
                self._cooldowns[provider] = time.monotonic() + cooldown
                log.warning(
                    "Provider %s failed (%s); cooling down %.0fs. Trying next...",
                    provider, e, cooldown,
                )
                last_exc = e
                continue
            except Exception as e:
                # Unexpected error - try next provider
                cooldown = self._cooldown_seconds(provider, 0)
                self._cooldowns[provider] = time.monotonic() + cooldown
                log.warning(
                    "Provider %s error (%s); cooling down %.0fs. Trying next...",
                    provider, e, cooldown,
                )
                last_exc = e
                continue

        # All providers exhausted
        raise RuntimeError(
            f"All {len(candidates)} available providers failed. Last error: {last_exc}"
        ) from last_exc

    async def _complete_for(
        self,
        provider: str,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LlmResult:
        """Dispatch a single provider call (used by the fallback loop)."""
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
        if provider == "xai":
            return await self._complete_xai(
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
        if provider == "bedrock":
            return await self._complete_bedrock(
                model=model, system=system, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
            )
        raise ProviderError(provider, 503, f"unknown provider: {provider}")

    @staticmethod
    def _cooldown_seconds(provider: str, status: int) -> float:
        """How long a provider sits out after a failure, by failure class.

        The durations are deliberately coarse: a bad key will not heal on its
        own, a quota wall needs minutes, and a transient 5xx needs only a short
        pause before the next attempt is reasonable.
        """
        if status in (401, 403):
            return 1800.0  # invalid/expired key or forbidden model
        if status == 429:
            return 600.0  # quota exhausted - give the window time to reset
        if status == 404:
            return 900.0  # model id does not exist on this provider
        if status >= 500 or status == 0:
            return 120.0  # transient outage or network failure
        return 300.0  # anything else (400 payload, etc.)

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

    # ---------------------------------------------------------------- xai (grok)

    async def _complete_xai(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LlmResult:
        """Complete via xAI's OpenAI-compatible Chat Completions API.

        Same wire format as Groq/OpenRouter, so the shared parser applies. A
        key without credits returns a 403 ("no credits or licenses"); that is
        a ProviderError and the chain falls through to the next provider, with
        xAI cooled down so the scheduler does not hammer it.
        """
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": _flatten_system(system)}] + messages
            if _flatten_system(system) else messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{XAI_API}/chat/completions"
        return await self._post_with_retry(
            "xai", url, model,
            headers={
                "Authorization": f"Bearer {self._settings.xai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            parse_fn=_parse_openai_compatible_response,
            timeout=XAI_TIMEOUT,
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

    # ---------------------------------------------------------------- bedrock

    async def _complete_bedrock(
        self,
        *,
        model: str,
        system: str | list[dict],
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> LlmResult:
        """Complete via Amazon Bedrock's Converse API.

        The converse API is the current, model-agnostic surface (Claude and
        others). It has no strict JSON mode, so callers requesting JSON rely on
        `extract_json`, exactly as they do for the other providers. The call is
        synchronous boto3 under the hood, so it runs in a worker thread.
        """
        import asyncio

        from app.aws import client

        bedrock = client("bedrock-runtime")
        if bedrock is None:
            raise ProviderError("bedrock", 503, "no AWS credentials configured")

        def _call() -> dict:
            import botocore  # noqa: PLC0415

            body: dict = {
                "modelId": model,
                "messages": [
                    {"role": "user" if m.get("role") != "assistant" else "assistant",
                     "content": [{"text": str(m.get("content") or "")}]}
                    for m in messages
                ],
                "inferenceConfig": {
                    "maxTokens": max_tokens,
                    "temperature": temperature,
                },
            }
            if system_text := _flatten_system(system):
                body["system"] = [{"text": system_text}]
            try:
                return bedrock.converse(**body)
            except botocore.exceptions.ClientError as exc:
                code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 500)
                raise ProviderError("bedrock", int(code), str(exc)) from exc

        payload = await asyncio.to_thread(_call)
        return _parse_bedrock_response(model, payload)

    # ---------------------------------------------------------------- anthropic

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
        """Complete via the Anthropic Messages API (official SDK).

        The SDK is an optional dependency and is imported lazily so a missing
        install only breaks the anthropic provider, not the whole client. The
        system prompt may carry cache_control blocks (see `cached_system`),
        which the SDK passes through verbatim. The Anthropic API has no strict
        JSON mode, so callers requesting JSON rely on `extract_json`, exactly
        as they do for the other providers.
        """
        try:
            from anthropic import (
                APIConnectionError,
                APIStatusError,
                AsyncAnthropic,
            )
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ProviderError(
                "anthropic", 503, "anthropic package not installed"
            ) from exc

        if self._anthropic_client is None:
            self._anthropic_client = AsyncAnthropic(
                api_key=self._settings.anthropic_api_key
            )
        client = self._anthropic_client

        # The Anthropic API only accepts user/assistant roles; the app never
        # sends anything else, but coerce defensively anyway.
        anon_messages = [
            {
                "role": "assistant" if str(m.get("role")) == "assistant" else "user",
                "content": str(m.get("content") or ""),
            }
            for m in messages
        ]

        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = await client.messages.create(
                    model=model,
                    system=system,
                    messages=anon_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                break
            except APIStatusError as exc:
                status = int(getattr(exc, "status_code", 0) or 0)
                if status not in _RETRYABLE or attempt == MAX_RETRIES:
                    raise ProviderError("anthropic", status or 500, str(exc)) from exc
                last_exc = exc
                log.warning(
                    "anthropic %s; retry %d/%d in %ds",
                    status, attempt, MAX_RETRIES, 2 ** attempt,
                )
                await _sleep(2 ** attempt)
            except APIConnectionError as exc:
                if attempt == MAX_RETRIES:
                    raise ProviderError("anthropic", 503, str(exc)) from exc
                last_exc = exc
                await _sleep(2 ** attempt)
        else:  # pragma: no cover - loop only exits via break or raise
            raise ProviderError("anthropic", 503, str(last_exc)) from last_exc

        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        usage = resp.usage
        return LlmResult(
            text=text,
            model=model,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_tokens=int(
                getattr(usage, "cache_read_input_tokens", 0) or 0
            ),
            cache_write_tokens=int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
            stop_reason=str(getattr(resp, "stop_reason", "") or ""),
        )

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
                delay = _retry_after(resp) or 2 ** attempt
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


def _parse_bedrock_response(model: str, payload: dict) -> LlmResult:
    """Parse a Bedrock Converse response."""
    text = ""
    stop = ""
    if isinstance(payload, dict):
        output = payload.get("output") or {}
        message = output.get("message") or {}
        parts = message.get("content") or []
        text = "".join(
            str(p.get("text") or "") for p in parts if isinstance(p, dict)
        )
        stop = str(output.get("stopReason") or "").lower()
        if stop == "max_tokens":
            stop = "max_tokens"
    usage = (payload or {}).get("usage") or {}
    return LlmResult(
        text=text,
        model=model,
        input_tokens=int(usage.get("inputTokens") or 0),
        output_tokens=int(usage.get("outputTokens") or 0),
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


def _retry_after(resp: httpx.Response) -> float:
    """Honour the provider's own backoff hint, capped so a job cannot stall.

    Gemini's free tier returns a Retry-After on 429 that is frequently much
    longer than exponential backoff would guess. Ignoring it meant retrying
    into the same quota wall three times and burning the whole retry budget.
    """
    raw = resp.headers.get("retry-after", "").strip()
    if not raw:
        return 0.0
    try:
        return min(float(raw), MAX_RETRY_AFTER)
    except ValueError:
        return 0.0  # HTTP-date form; fall back to exponential backoff.


_client: LlmClient | None = None


def get_llm() -> LlmClient:
    global _client  # noqa: PLW0603
    if _client is None:
        _client = LlmClient()
    return _client
