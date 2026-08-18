"""Settings-level tests for LLM enablement, email backend, and warnings."""

from app.config import Settings

# The provider chain in app.llm.client._build_provider_order accepts xAI,
# Gemini, OpenRouter, Groq, Ollama, Anthropic and Bedrock. `llm_enabled` must
# agree with that chain (minus Ollama, whose default host points at nothing
# until the operator actually runs a local server) or the dashboard keeps
# warning "LLM not configured" even though scoring and tailoring work.

LLM_KEY_NAMES = (
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "ANTHROPIC_API_KEY",
)


def test_llm_enabled_false_without_keys() -> None:
    assert not Settings().llm_enabled


def test_llm_enabled_openrouter_key() -> None:
    assert Settings(openrouter_api_key="sk-or-v1-test").llm_enabled


def test_llm_enabled_groq_key() -> None:
    assert Settings(groq_api_key="gsk_test").llm_enabled


def test_llm_enabled_other_providers() -> None:
    assert Settings(xai_api_key="xai-test").llm_enabled
    assert Settings(gemini_api_key="gem-test").llm_enabled
    assert Settings(anthropic_api_key="sk-ant-test").llm_enabled
    assert Settings(aws_access_key_id="AKIATEST").llm_enabled


def test_no_llm_warning_when_openrouter_configured() -> None:
    s = Settings(openrouter_api_key="sk-or-v1-test")
    assert not any(w.startswith("No LLM API key") for w in s.startup_warnings())


def test_no_llm_warning_when_groq_configured() -> None:
    s = Settings(groq_api_key="gsk_test")
    assert not any(w.startswith("No LLM API key") for w in s.startup_warnings())


def test_llm_warning_lists_all_providers() -> None:
    s = Settings()
    llm_warnings = [w for w in s.startup_warnings() if w.startswith("No LLM API key")]
    assert llm_warnings
    for key in LLM_KEY_NAMES:
        assert key in llm_warnings[0]


# ---------------------------------------------------------------------------
# Email backend: Resend preferred, SMTP legacy fallback.
# ---------------------------------------------------------------------------


def test_email_disabled_without_backend() -> None:
    assert not Settings().email_enabled


def test_email_enabled_with_resend_key() -> None:
    assert Settings(resend_api_key="re_test").email_enabled


def test_email_enabled_with_smtp() -> None:
    assert Settings(smtp_host="smtp.gmail.com", smtp_user="me@gmail.com").email_enabled


def test_email_disabled_smtp_without_user() -> None:
    assert not Settings(smtp_host="smtp.gmail.com").email_enabled


def test_email_warning_when_disabled() -> None:
    s = Settings()
    assert any("No email backend configured" in w for w in s.startup_warnings())


def test_no_email_warning_when_resend_configured() -> None:
    s = Settings(resend_api_key="re_test")
    assert not any("No email backend" in w for w in s.startup_warnings())
