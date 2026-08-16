"""Tests for the LLM fallback chain's circuit breaker (provider cooldowns).

Every provider in the chain carries a per-provider cooldown after a failure.
Without it, a dead key or an exhausted quota makes every pipeline call burn its
full retry budget, which under the scheduler becomes a retry storm that hammers
SQLite ("database is locked") and stalls login writes. These tests exercise the
breaker with stubbed dispatchers, so no network traffic is involved.
"""

from __future__ import annotations

import time

import pytest

from app.llm.client import (
    LlmClient,
    ProviderError,
    _parse_openai_compatible_response,
)


class _StubResult:
    def __init__(self, text: str = "ok") -> None:
        self.text = text
        self.model = "stub"
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.stop_reason = "end_turn"


def _client(providers: list[str], dispatcher) -> LlmClient:
    """An LlmClient with a forced provider order and a stubbed dispatcher."""
    c = LlmClient()
    c._provider_order = list(providers)
    c._complete_for = dispatcher  # noqa: SLF001 - test seam
    return c


def _always_ok(provider: str, **_kwargs) -> _StubResult:
    return _StubResult()


@pytest.mark.asyncio
async def test_failure_cools_down_provider_and_skips_it_next_call():
    calls = {"a": 0, "b": 0}

    async def dispatcher(provider: str, **_kwargs):
        calls[provider] += 1
        if provider == "a":
            raise ProviderError("a", 429, "quota exceeded")
        return _StubResult()

    c = _client(["a", "b"], dispatcher)

    # First call: "a" fails and is cooled down, "b" succeeds.
    result = await c.complete(model="m", system="", messages=[])
    assert result.text == "ok"
    assert calls == {"a": 1, "b": 1}
    assert "a" in c._cooldowns
    assert "b" not in c._cooldowns  # success lifts any cooldown

    # Second call: "a" sits out its cooldown; "b" is used again.
    await c.complete(model="m", system="", messages=[])
    assert calls == {"a": 1, "b": 2}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "min_cooldown"),
    [
        (401, 1800.0),  # bad / expired key
        (403, 1800.0),  # forbidden
        (429, 600.0),  # quota wall
        (404, 900.0),  # unknown model
        (503, 120.0),  # transient outage
        (400, 300.0),  # malformed request
    ],
)
async def test_cooldown_duration_scales_with_failure_class(status, min_cooldown):
    async def dispatcher(provider: str, **_kwargs):
        raise ProviderError(provider, status, "boom")

    c = _client(["a"], dispatcher)

    with pytest.raises(RuntimeError):
        await c.complete(model="m", system="", messages=[])

    remaining = c._cooldowns["a"] - time.monotonic()
    assert remaining >= min_cooldown - 1.0
    assert remaining < min_cooldown + 60.0


@pytest.mark.asyncio
async def test_all_cooled_down_fails_fast_without_trying():
    """When every provider is cooling down, the chain fails immediately
    instead of still burning retry budget on the soonest-recovering provider —
    that override is what let a dead chain stall the scheduler."""
    calls = {"a": 0, "b": 0}

    async def dispatcher(provider: str, **_kwargs):
        calls[provider] += 1
        raise ProviderError(provider, 401, "bad key")

    c = _client(["a", "b"], dispatcher)

    with pytest.raises(RuntimeError):
        await c.complete(model="m", system="", messages=[])
    assert calls == {"a": 1, "b": 1}

    # Both providers are now cooling down; a further call must not dispatch.
    with pytest.raises(RuntimeError, match="cooling down"):
        await c.complete(model="m", system="", messages=[])
    assert calls == {"a": 1, "b": 1}


@pytest.mark.asyncio
async def test_recovered_provider_has_cooldown_lifted_on_success():
    calls = {"a": 0}

    async def dispatcher(provider: str, **_kwargs):
        calls[provider] += 1
        if calls[provider] == 1:
            raise ProviderError(provider, 429, "quota exceeded")
        return _StubResult()

    c = _client(["a"], dispatcher)

    with pytest.raises(RuntimeError):
        await c.complete(model="m", system="", messages=[])
    assert "a" in c._cooldowns

    # Simulate the quota window resetting: cooldown expires, next call succeeds
    # and the breaker is cleared so the provider is used immediately again.
    c._cooldowns["a"] = time.monotonic() - 1
    await c.complete(model="m", system="", messages=[])
    assert "a" not in c._cooldowns

    await c.complete(model="m", system="", messages=[])
    assert calls == {"a": 3}


@pytest.mark.asyncio
async def test_no_providers_configured_fails_fast():
    c = _client([], _always_ok)
    assert not c.available
    with pytest.raises(RuntimeError):
        await c.complete(model="m", system="", messages=[])


# ---------------------------------------------------------------- xai (grok)


def test_xai_is_first_in_provider_order(monkeypatch):
    c = LlmClient()
    monkeypatch.setattr(c._settings, "xai_api_key", "test-key")
    order = c._build_provider_order()
    assert order[0] == "xai"


@pytest.mark.asyncio
async def test_xai_provider_wiring(monkeypatch):
    """The xAI provider hits the OpenAI-compatible endpoint with the right
    auth header and payload shape; no network involved."""
    c = LlmClient()
    monkeypatch.setattr(c._settings, "xai_api_key", "test-key")
    captured: dict = {}

    async def fake_post(provider, url, model, *, params=None, headers=None,
                        json=None, parse_fn=None, timeout=0.0):
        captured.update(provider=provider, url=url, headers=headers or {},
                        json=json or {}, model=model)
        return _StubResult()

    monkeypatch.setattr(c, "_post_with_retry", fake_post)

    await c._complete_xai(
        model="grok-4.6", system="sys",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64, temperature=0.1, json_mode=True,
    )
    assert captured["provider"] == "xai"
    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "grok-4.6"
    assert captured["json"]["response_format"] == {"type": "json_object"}


def test_parse_xai_response():
    payload = {
        "choices": [{"message": {"content": "{\"ok\": true}"},
                      "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9},
    }
    r = _parse_openai_compatible_response("grok-4.6", payload)
    assert r.text == "{\"ok\": true}"
    assert r.input_tokens == 5
    assert r.output_tokens == 9
    assert not r.truncated
